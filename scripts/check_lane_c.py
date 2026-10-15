#!/usr/bin/env python3
"""Drive both ways from the report point to finishing on the plant world, and film lane C.

    python3 scripts/check_lane_c.py --out /tmp/lane-c        # on the plant world already built

The plant world (scripts/plant_xodr.py) has two ways to the yard: round the finishing corner, or
along lane C across the middle. Route choice (flows/route.py) sends a car one way or the other by
the points it gives the driving system, so this checks that each way is real: BehaviorAgent, given
the same points, drives there (through the corner points, or along road 20, lane C) and reaches
finishing. With --film, a camera behind the car films the lane C drive.
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import sys
from pathlib import Path

import carla

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_plant_world import ffmpeg  # noqa: E402

WAYS = {"corner": ["finishing_corner.p1", "finishing_corner.p2", "finishing_corner.p3", "finishing"],
        "lane_c": ["lane_c", "finishing"]}


def drive(world, pts: dict, way: str, film: Path | None, timeout_s: float = 240.0) -> dict:
    from agents.navigation.behavior_agent import BehaviorAgent
    start = pts["report_point"]
    bp = world.get_blueprint_library().find("vehicle.lincoln.mkz_2020")
    bp.set_attribute("color", "0,80,200")
    bp.set_attribute("role_name", f"lane-c-check-{way}")
    car = world.spawn_actor(bp, carla.Transform(carla.Location(start["x"], start["y"], start["z"]),
                                                carla.Rotation(yaw=start["yaw"])))
    m = world.get_map()
    cams, writer, frames = [], None, queue.Queue()
    roads, travelled = [], 0.0
    try:
        world.tick()
        if film is not None:
            bp_cam = world.get_blueprint_library().find("sensor.camera.rgb")
            bp_cam.set_attribute("image_size_x", "1280")
            bp_cam.set_attribute("image_size_y", "720")
            bp_cam.set_attribute("fov", "90")
            cam = world.spawn_actor(bp_cam, carla.Transform(carla.Location(x=-9, z=4.5), carla.Rotation(pitch=-14)),
                                    attach_to=car)
            cam.listen(frames.put)
            cams = [cam]
            writer = ffmpeg(film, 1280, 720, 20.0)
        agent = BehaviorAgent(car, behavior="cautious")
        legs = [pts[p] for p in WAYS[way]]
        agent.set_destination(carla.Location(legs[0]["x"], legs[0]["y"], legs[0]["z"]))
        last, ticks = car.get_location(), 0
        while ticks < timeout_s / 0.05:
            car.apply_control(agent.run_step())
            frame = world.tick()
            ticks += 1
            here = car.get_location()
            travelled += here.distance(last)
            last = here
            wp = m.get_waypoint(here, project_to_road=True, lane_type=carla.LaneType.Driving)
            if wp is not None and (not roads or roads[-1] != wp.road_id):
                roads.append(wp.road_id)
            if writer is not None:
                img = frames.get(timeout=10.0)
                while img.frame < frame:
                    img = frames.get(timeout=10.0)
                if ticks % 2 == 0:
                    writer.stdin.write(bytes(img.raw_data))
            if agent.done():
                legs.pop(0)
                if not legs:
                    break
                agent.set_destination(carla.Location(legs[0]["x"], legs[0]["y"], legs[0]["z"]))
        end, goal = car.get_location(), pts["finishing"]
        return {"way": way, "reached": not legs, "sim_s": round(ticks * 0.05, 1), "travelled_m": round(travelled, 1),
                "roads": roads, "end_off_finishing_m": round(math.dist((end.x, end.y), (goal["x"], goal["y"])), 2),
                "film": str(film) if film else None}
    finally:
        if writer is not None:
            writer.stdin.close()
            writer.wait()
        for c in cams:
            c.stop()
            c.destroy()
        car.destroy()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--points", type=Path, default=Path("/tmp/marshal-plant-world/points.json"))
    ap.add_argument("--out", type=Path, default=Path("/tmp/lane-c"))
    ap.add_argument("--film", action="store_true")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    client = carla.Client("localhost", 2000)
    client.set_timeout(60.0)
    world = client.get_world()
    pts = json.loads(args.points.read_text())
    original = world.get_settings()
    settings = world.get_settings()
    settings.synchronous_mode, settings.fixed_delta_seconds = True, 0.05
    world.apply_settings(settings)
    try:
        for way in ("corner", "lane_c"):
            film = args.out / f"{way}.mp4" if args.film else None
            print(json.dumps(drive(world, pts, way, film)), flush=True)
    finally:
        world.apply_settings(original)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
