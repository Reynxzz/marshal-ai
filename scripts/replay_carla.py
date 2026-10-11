"""Replay a CARLA recording and film it, one still camera per shot, into an MP4.

Runs on the CARLA host, as the only client, with the replay stepped in synchronous mode: one
frame per tick, so nothing is dropped however slow the encoder is. Cars are found by their
role_name (the VIN).

Two passes over the recording:
  1. scout, with no camera: note which car drives when and along what path.
  2. film: a wide opening still of all the cars, then for each drive a camera set down beside
     that stretch of road, framed to fit it, so the car crosses a still frame.

Why still cameras: the plant's cars drive at walking pace, so a camera riding along with one
shows a car frozen mid-frame; and moving a camera during a replay (panning it, or teleporting
it to the next shot) segfaults the CARLA 0.9.16 server, so each shot gets a camera of its own. Most of a run is cars
standing still while Gemini and the records do their part, so those stretches are cut.
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from carla_recorder import DEFAULT_DIR, duration_s, recording_path  # noqa: E402
from film_labels import Labeller, plant_points, run_title, zone_for  # noqa: E402

SETTLE_TICKS = 10
MOVING_M_PER_TICK = 0.01  # 0.2 m/s at 0.05 s ticks; a parked car jitters far less
TELEPORT_M_PER_TICK = 1.0  # 72 km/h; more is the replayer spawning or removing a car, not driving
OPENING_AFTER_TICKS = 4  # let freshly replayed cars drop onto the road first
SHOT_MAX_M = 22.0  # a longer drive is split, so the car stays big and the road around it small
SHOT_MIN_TICKS = 20  # 1 s; shorter is a car settling as the replayer spawns it, not a drive, and
                     # moving the camera about while cars are spawned is what crashes the server

Point = tuple[float, float, float]


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


LAB_ROLE = "marshal-lab"  # marshal_ai.lab.compiler tags every actor of a lab scenario with it


def vin_cars(world) -> dict:
    """The plant's cars by VIN, and a lab scenario's vehicles; anything else is scenery."""
    cars = {}
    for actor in world.get_actors().filter("vehicle.*"):
        role = actor.attributes.get("role_name", "")
        if role.startswith("VIN-"):
            cars[role] = actor
        elif role == LAB_ROLE:
            cars[f"{LAB_ROLE}-{actor.id}"] = actor
    return cars


class Director:
    """Decides, tick by tick, which car is the subject and whether the moment is worth keeping.

    With no fixed VIN it stays on a car while that car drives and cuts to another one as soon as
    it starts. A moment is kept while something moves, plus `grace_ticks` after it stops, so the
    cut does not come mid-brake.
    """

    def __init__(self, follow: str | None = None, keep_idle: bool = False,
                 grace_ticks: int = 20):
        self.follow = follow
        self.keep_idle = keep_idle
        self.grace_ticks = grace_ticks
        self.target: str | None = None
        self._last: dict[str, Point] = {}
        self._idle = grace_ticks + 1  # start at the first movement, not at the spawn

    def update(self, positions: dict[str, Point]) -> tuple[str | None, bool]:
        step = {vin: math.dist(pos, self._last[vin]) for vin, pos in positions.items()
                if vin in self._last}
        self._last = dict(positions)
        moving = {vin: d for vin, d in step.items()
                  if MOVING_M_PER_TICK < d <= TELEPORT_M_PER_TICK}
        if self.follow:
            self.target = self.follow if self.follow in positions else None
            moving = {v: d for v, d in moving.items() if v == self.follow}
        else:
            if self.target not in positions:
                self.target = None
            if moving and self.target not in moving:
                self.target = max(moving, key=moving.get)
            if self.target is None and positions:
                self.target = sorted(positions)[0]
        self._idle = 0 if moving else self._idle + 1
        return self.target, self.keep_idle or self._idle <= self.grace_ticks


@dataclass
class Shot:
    start: int  # first tick, counted after the settle ticks
    end: int    # last tick, inclusive
    vin: str | None
    points: list[Point] = field(default_factory=list)
    still: bool = False  # the opening: one frame, held
    yaw: float = 0.0     # the opening looks along the cars' heading


def plan_shots(samples: list[tuple[str | None, bool, Point | None]],
               max_len_m: float = SHOT_MAX_M, min_ticks: int = SHOT_MIN_TICKS) -> list[Shot]:
    """Group the kept ticks into shots: one per car per drive, split when it gets too long,
    dropped when too short to be a drive.

    `samples[tick]` is (subject VIN, keep this tick, the subject's position).
    """
    shots: list[Shot] = []
    current: Shot | None = None
    for tick, (vin, keep, pos) in enumerate(samples):
        if not keep or vin is None or pos is None:
            current = None
            continue
        too_long = current is not None and math.dist(current.points[0][:2], pos[:2]) > max_len_m
        if current is None or vin != current.vin or too_long:
            current = Shot(tick, tick, vin)
            shots.append(current)
        current.end = tick
        current.points.append(pos)
    return [s for s in shots if s.end - s.start + 1 >= min_ticks]


def look_at(camera: Point, target: Point) -> tuple[float, float]:
    """Pitch and yaw, in degrees, that point a camera at `camera` toward `target`."""
    dx, dy, dz = (t - c for t, c in zip(target, camera))
    return (math.degrees(math.atan2(dz, math.hypot(dx, dy))),
            math.degrees(math.atan2(dy, dx)))


def side_pose(points: list[Point], fov_deg: float = 90.0, side: str = "right"
              ) -> tuple[Point, float, float]:
    """A still camera beside a stretch of road, a little ahead, far enough back to fit it all.

    `side` is relative to the direction of travel. Returns location, pitch and yaw. The car
    enters on one side of the frame and drives across.
    """
    first, last = points[0], points[-1]
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    cz = sum(p[2] for p in points) / len(points)
    dx, dy = last[0] - first[0], last[1] - first[1]
    length = math.hypot(dx, dy)
    ux, uy = (dx / length, dy / length) if length > 0.5 else (1.0, 0.0)
    span = max(length, 8.0)
    back = span / 2 / math.tan(math.radians(fov_deg) / 2) + 6.0
    ahead = span * 0.25
    up = 8.0 + 0.3 * back  # over the medians' fences, and the horizon (the city) low in the frame
    sign = 1.0 if side == "right" else -1.0
    # right of travel is (uy, -ux) in CARLA's left-handed x/y
    location = (cx + sign * uy * back + ux * ahead, cy - sign * ux * back + uy * ahead, cz + up)
    pitch, yaw = look_at(location, (cx, cy, cz + 0.8))
    return location, pitch, yaw


def overview_pose(points: list[Point], yaw_deg: float,
                  margin: float = 12.0) -> tuple[Point, float]:
    """A raised spot behind the cars, facing along `yaw_deg`, far enough back to fit them all.

    Returns the camera location and its pitch. Kept modest on purpose: a camera high over the map
    makes the server stream most of Town05 in at once, and it segfaults.
    """
    yaw = math.radians(yaw_deg)
    fx, fy = math.cos(yaw), math.sin(yaw)
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    cz = sum(p[2] for p in points) / len(points)
    spread = max((math.hypot(p[0] - cx, p[1] - cy) for p in points), default=0.0)
    back = spread + margin
    up = min(30.0, 0.6 * back + 4.0)
    location = (cx - fx * back, cy - fy * back, cz + up)
    return location, -math.degrees(math.atan2(up, back))


def ffmpeg_writer(out: Path, width: int, height: int, fps: float) -> subprocess.Popen:
    import imageio_ffmpeg

    out.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{width}x{height}", "-r", f"{fps:g}",
         "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)],
        stdin=subprocess.PIPE,
    )


def next_image(images: queue.Queue, frame: int):
    while True:
        image = images.get(timeout=10.0)
        if image.frame >= frame:
            return image


DRESSING_ROLE = "marshal-dressing"  # scripts/dress_plant.py tags the plant's scenery with it


def purge_dressing(world) -> int:
    """Remove the plant dressing from the live world.

    The recording holds the dressing, and the replayer re-creates it. Left in place, the live copy
    stood under the replayed one, and the replayer did not remove everything it had made: after two
    filmings 155 dressing actors had built up, some drifted onto the yard slots.

    The plant world's factory halls (static meshes) are left: the replayer cannot place them (see
    drop_stranded_meshes), so the ones place_halls put in the live world are the ones filmed.
    """
    tagged = [a for a in world.get_actors()
              if a.attributes.get("role_name") == DRESSING_ROLE and a.type_id != "static.prop.mesh"]
    for a in tagged:
        a.destroy()
    return len(tagged)


def drop_stranded_meshes(world) -> int:
    """The replayer spawns each actor 1 km above the origin and then moves it into place, and a static
    mesh (a plant-world factory hall) cannot be moved: left there, it blocks every later spawn, the
    filmed cars included. dress_plant.py spawns the halls last, so after the first tick they are the
    only thing created; remove them before any car is."""
    stranded = [a for a in world.get_actors().filter("static.prop.mesh") if a.get_location().z > 500.0]
    for a in stranded:
        a.destroy()
    return len(stranded)


def place_halls(world) -> int:
    """The halls, in the live world, where dress_plant.py put them for the run: the replay's own copies
    are stranded, and loading the plant world for the replay rebuilt it empty. Once per world: the
    second pass finds them still there (purge_dressing leaves them)."""
    # on the ground: the stranded copies just destroyed are still listed until the next tick
    if any(a.get_location().z < 500.0 for a in world.get_actors().filter("static.prop.mesh")):
        return 0
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import dress_plant
    d = dress_plant.Dresser(world)
    dress_plant.plant_halls(d, world.get_map(), dress_plant.road_lengths())
    return len(d.spawned)


def start_replay(client, world, path: str, args) -> None:
    client.set_replayer_ignore_spectator(True)
    purge_dressing(world)
    world.tick()
    client.replay_file(path, args.start, args.duration, 0, False)
    world.tick()
    if drop_stranded_meshes(world):
        place_halls(world)
    # Spawning a sensor while the replayer is still creating the recorded actors can segfault the
    # server, so let it settle first. Both passes do the same, so their ticks line up.
    for _ in range(SETTLE_TICKS):
        world.tick()


def scout(client, world, path: str, ticks: int, args) -> list[Shot]:
    """Pass 1: replay without a camera and plan the shots."""
    director = Director(args.follow, args.keep_idle)
    samples: list[tuple[str | None, bool, Point | None]] = []
    opening: Shot | None = None
    cars_seen = 0
    start_replay(client, world, path, args)
    for tick in range(ticks):
        cars = vin_cars(world)
        positions = {}
        for vin, actor in cars.items():
            loc = actor.get_location()
            positions[vin] = (loc.x, loc.y, loc.z)
        cars_seen = cars_seen + 1 if cars else 0
        if opening is None and args.opening_s > 0 and cars_seen > OPENING_AFTER_TICKS:
            yaw = next(iter(cars.values())).get_transform().rotation.yaw
            opening = Shot(tick, tick, None, list(positions.values()), still=True, yaw=yaw)
        vin, keep = director.update(positions)
        samples.append((vin, keep, positions.get(vin) if vin else None))
        world.tick()
    client.stop_replayer(False)
    world.tick()
    purge_dressing(world)
    shots = plan_shots(samples)
    if opening is not None:
        shots = [s for s in shots if s.end > opening.start]
        for s in shots:
            s.start = max(s.start, opening.start + 1)
        shots.insert(0, opening)
    return shots


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="recording name given to carla_recorder.py")
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--follow", help="only film this VIN (default: whichever car is driving)")
    ap.add_argument("--opening-s", type=float, default=2.5,
                    help="seconds of the wide opening still of all the cars (0 = none)")
    ap.add_argument("--keep-idle", action="store_true",
                    help="keep the stretches where no car moves (cut by default)")
    ap.add_argument("--out", default=None, help="MP4 path (default /tmp/marshal-replays/NAME.mp4)")
    ap.add_argument("--start", type=float, default=0.0, help="seconds into the recording")
    ap.add_argument("--duration", type=float, default=0.0, help="seconds to film (0 = to the end)")
    ap.add_argument("--speed", type=int, default=4, help="playback speed-up, by keeping every Nth frame")
    ap.add_argument("--side", choices=("right", "left"), default="right",
                    help="which side of the road, seen in the direction of travel, to film from; "
                         "in Town05 a camera left of the yard lane, looking west, segfaults the "
                         "server, so keep the default there")
    ap.add_argument("--delta", type=float, default=0.05, help="replay step, seconds")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--sun-altitude", type=float, default=45.0,
                    help="daylight for the replay; the recording does not restore the weather")
    ap.add_argument("--title", default=None, help="label for the film (default: from the recording name)")
    ap.add_argument("--no-labels", action="store_true", help="do not burn in the run and zone labels")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    args = ap.parse_args(argv)
    if args.speed < 1 or args.delta <= 0:
        raise SystemExit("speed must be >= 1 and delta > 0")

    import carla

    path = recording_path(args.name, args.dir)
    out = Path(args.out or f"/tmp/marshal-replays/{args.name}.mp4")
    client = carla.Client(args.host, args.port)
    client.set_timeout(30.0)
    world = client.get_world()

    total = duration_s(client.show_recorder_file_info(path, False))
    if total is None:
        raise SystemExit(f"could not read the recording {path}; was it recorded on this server?")
    length = args.duration or max(0.0, total - args.start)
    ticks = max(0, int(length / args.delta) - SETTLE_TICKS)
    emit({"type": "replay.start", "file": path, "recorded_s": total, "out": str(out)})

    original_settings = world.get_settings()
    original_weather = world.get_weather()
    camera = None
    writer = None
    try:
        settings = world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = args.delta
        world.apply_settings(settings)
        weather = world.get_weather()
        weather.sun_altitude_angle = args.sun_altitude
        world.set_weather(weather)

        shots = scout(client, world, path, ticks, args)
        if not any(not s.still for s in shots):
            raise SystemExit("no car moved in this recording; use --keep-idle to film it anyway")
        for s in shots:
            emit({"type": "replay.shot", "vin": s.vin, "still": s.still,
                  "from_s": round(args.start + (s.start + SETTLE_TICKS) * args.delta, 1),
                  "to_s": round(args.start + (s.end + SETTLE_TICKS) * args.delta, 1)})

        bp = world.get_blueprint_library().find("sensor.camera.rgb")
        bp.set_attribute("image_size_x", str(args.width))
        bp.set_attribute("image_size_y", str(args.height))
        bp.set_attribute("fov", "90")
        images: queue.Queue = queue.Queue()
        writer = ffmpeg_writer(out, args.width, args.height, 1.0 / args.delta)
        labeller = Labeller(args.width, args.height)
        if args.no_labels:
            labeller.ok = False
        elif not labeller.ok:
            emit({"type": "replay.no_labels", "why": "Pillow is not installed"})
        named, title = plant_points(), args.title or run_title(args.name)

        def caption(s: Shot) -> tuple[str | None, str | None]:
            if s.still:
                return "Plant overview", None
            who = s.vin if (s.vin or "").startswith("VIN-") else "Test-lab scenario car"
            return zone_for(s.points, named), who

        start_replay(client, world, path, args)
        emit({"type": "replay.filming"})
        written = 0
        shot_index = 0
        for tick in range(min(ticks, shots[-1].end + 1)):
            shot = shots[shot_index] if shot_index < len(shots) else None
            filming = shot is not None and shot.start <= tick <= shot.end
            if shot is not None and tick == shot.start:
                if shot.still:
                    yaw = shot.yaw
                    (x, y, z), pitch = overview_pose(shot.points, yaw)
                else:
                    (x, y, z), pitch, yaw = side_pose(shot.points, side=args.side)
                pose = carla.Transform(carla.Location(x=x, y=y, z=z),
                                       carla.Rotation(pitch=pitch, yaw=yaw))
                # A fresh camera per shot: teleporting one a few tens of metres with set_transform
                # during a replay segfaults the 0.9.16 server, while spawning one there does not.
                if camera is not None:
                    camera.stop()
                    camera.destroy()
                camera = world.spawn_actor(bp, pose)
                camera.listen(images.put)
                emit({"type": "replay.camera", "tick": tick, "at": [round(v, 1) for v in (x, y, z)],
                      "pitch": round(pitch, 1), "yaw": round(yaw, 1)})
            frame = world.tick()
            if camera is not None:
                image = next_image(images, frame)  # always drained, or frames pile up in memory
                if filming and shot.still:
                    still = labeller.draw(bytes(image.raw_data), title, *caption(shot))
                    held = round(args.opening_s / args.delta)
                    for _ in range(held):
                        writer.stdin.write(still)
                    written += held
                elif filming and (tick - shot.start) % args.speed == 0:
                    writer.stdin.write(labeller.draw(bytes(image.raw_data), title, *caption(shot)))
                    written += 1
            if shot is not None and tick >= shot.end:
                shot_index += 1
            if tick % 100 == 0:
                emit({"type": "replay.progress", "tick": tick, "frames": written})
        writer.stdin.close()
        if writer.wait() != 0:
            raise SystemExit("ffmpeg failed to write the video")
        emit({"type": "replay.done", "out": str(out), "shots": len(shots), "frames": written,
              "video_s": round(written * args.delta, 1), "speed": args.speed})
        return 0
    finally:
        if writer is not None and writer.poll() is None:
            writer.kill()
        # Each step on its own: a failing one must not hide the error that got us here, and the
        # world must still go back to asynchronous mode, or every later run hangs.
        steps = [("stop_replayer", lambda: client.stop_replayer(False)),
                 ("purge_dressing", lambda: (world.tick(), purge_dressing(world))),
                 ("restore_weather", lambda: world.set_weather(original_weather)),
                 ("restore_settings", lambda: world.apply_settings(original_settings))]
        if camera is not None:
            steps.insert(0, ("destroy_camera", lambda: (camera.stop(), camera.destroy())))
        for name, step in steps:
            try:
                step()
            except RuntimeError as exc:
                emit({"type": "replay.cleanup_failed", "step": name, "error": str(exc)})


if __name__ == "__main__":
    sys.exit(main())
