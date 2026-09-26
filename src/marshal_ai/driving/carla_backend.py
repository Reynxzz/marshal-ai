"""CARLA BehaviorAgent playing the existing, validated driving system.

Runs on the GPU VM next to the CARLA server. Needs the `carla` client package and
CARLA's PythonAPI `agents` package on the path:

    export PYTHONPATH=$CARLA_ROOT/PythonAPI/carla:$PYTHONPATH

Marshal only ever sends missions; this module is where they become BehaviorAgent
destinations. The agent's own collision and hazard stops are never overridden.

Test track routines switch the car's real lights, so the track camera sees them. A car
in `faults` reports a lamp ON in telemetry while leaving it dark, the "dead bulb" case.

Flow 2's trigger: when BehaviorAgent stops short of its destination and something is in
front of the car, the driving system raises assist.request with a real forward frame and
waits for a human, exactly as the fake backend does.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from marshal_ai.artifacts import Artifacts, NoArtifacts
from marshal_ai.carla_connection import connect_carla
from marshal_ai.driving.api import EventHandler, emit
from marshal_ai.driving.messages import (
    AssistChoose, AssistRequest, Command, MissionDone, MissionGo, MissionHold, MissionResume, Status, TrackStep,
)
from marshal_ai.driving.routines import RoutineStep, routine_steps
from marshal_ai.fakeworld import GATE_VIEWS
from marshal_ai.llm import Media
from marshal_ai.plant import PlantMap

log = logging.getLogger(__name__)

try:
    import carla
except ImportError:  # local development without the compiled CARLA client
    carla = None

try:
    from agents.navigation.behavior_agent import BehaviorAgent
except ImportError:  # bundled under CARLA_ROOT/PythonAPI/carla, not in the PyPI client wheel
    BehaviorAgent = None


@dataclass
class _Vehicle:
    vin: str
    actor: object                          # carla.Vehicle
    agent: object                          # BehaviorAgent
    location: str
    mission: MissionGo | None = None
    remaining: list[str] = field(default_factory=list)
    held: bool = False
    hold_left_s: float | None = None
    assist_case: str | None = None
    routine_queue: list[RoutineStep] = field(default_factory=list)
    routine_done: bool = False
    step_left_s: float = 0.0
    stalled_s: float = 0.0                 # how long the agent has been stopped mid-mission
    assists_raised: int = 0


# The options the driving system offers when it stops. The desk may only choose among these;
# it never invents a path. "wait" first so a rule-bound desk always has a safe choice.
ASSIST_OPTIONS = ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"]

STEP_LIGHTS = {
    "headlights": "LowBeam", "indicators": "LeftBlinker", "brake_lights": "Brake", "reverse": "Reverse",
}


class CarlaDrivingSystem:
    def __init__(self, plant_map: PlantMap, host: str = "localhost", port: int = 2000,
                 fixed_delta_s: float = 0.05, behavior: str = "cautious",
                 vehicle_blueprint: str = "vehicle.lincoln.mkz_2020",
                 timeout_s: float = 20.0, expected_version: str = "0.9.16",
                 auto_assist: bool = True, blocked_after_s: float = 5.0,
                 blocked_speed_kmh: float = 0.6, obstacle_range_m: float = 14.0,
                 assist_options: list[str] | None = None):
        if carla is None:
            raise RuntimeError(
                "CARLA client not available. Install the 0.9.16 wheel bundled with the server "
                "package, or use MARSHAL_DRIVING=fake."
            )
        if BehaviorAgent is None:
            raise RuntimeError(
                "CARLA BehaviorAgent not available. Add $CARLA_ROOT/PythonAPI/carla to "
                "PYTHONPATH before using MARSHAL_DRIVING=carla."
            )
        self.map = plant_map
        self.fixed_delta_s = fixed_delta_s
        self.behavior = behavior
        self.vehicle_blueprint = vehicle_blueprint
        self.client, self.server_info = connect_carla(
            host, port, timeout_s, expected_version, client_factory=carla.Client
        )
        self.world = self.client.get_world()
        self._tick_lock = threading.Lock()
        self.vehicles: dict[str, _Vehicle] = {}
        self._handlers: list[EventHandler] = []
        self._task: asyncio.Task | None = None
        self._since_status = 0.0
        self.faults: dict[str, set[str]] = {}      # vin -> routine steps whose lamp stays dark
        # case_id -> forward frame taken when the car stopped, so the desk can show Gemini real
        # pixels. AssistRequest only carries a URI, and Vertex AI cannot read a gs:// path that
        # does not exist, which is how flow 3 first failed.
        self.assist_clips: dict[str, bytes] = {}
        self.blocked_after_s = blocked_after_s
        self.blocked_speed_kmh = blocked_speed_kmh
        self.obstacle_range_m = obstacle_range_m
        self.assist_options = list(assist_options) if assist_options else list(ASSIST_OPTIONS)
        self.auto_assist = auto_assist
        # Reusable capture cameras, keyed by pose; see capture()/close_cameras().
        self._cameras: dict[tuple, tuple] = {}
        # Replaced by the runtime when MARSHAL_ARTIFACTS=gcs, so the gs:// URI a record
        # carries points at the actual photo behind the verdict.
        self.artifacts: Artifacts = NoArtifacts()

    def transform(self, point: str):
        spec = self.map.points[point].carla
        if not spec:
            raise ValueError(f"map point {point} has no CARLA transform yet (plant_map.yaml)")
        return carla.Transform(
            carla.Location(x=spec["x"], y=spec["y"], z=spec.get("z", 0.5)),
            carla.Rotation(yaw=spec.get("yaw", 0.0)),
        )

    # plant side: a finished car rolls off the line

    def spawn(self, vin: str, at: str = "line_end", colour: str | None = None) -> None:
        bp = self.world.get_blueprint_library().find(self.vehicle_blueprint)
        if colour and bp.has_attribute("color"):
            bp.set_attribute("color", colour)            # "r,g,b"
        bp.set_attribute("role_name", vin)
        actor = self.world.spawn_actor(bp, self.transform(at))
        try:
            # A freshly spawned actor can report the origin until the server publishes one frame.
            # BehaviorAgent reads the actor transform in its constructor, so synchronize first.
            if self.world.get_settings().synchronous_mode:
                self._tick_world()
            else:
                self.world.wait_for_tick(seconds=10.0)
            agent = BehaviorAgent(actor, behavior=self.behavior)
        except Exception:
            actor.destroy()
            raise
        self.vehicles[vin] = _Vehicle(vin=vin, actor=actor, agent=agent, location=at)

    # DrivingSystem

    def on_event(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    async def send(self, command: Command) -> None:
        if isinstance(command, MissionGo):
            v = self.vehicles[command.vin]
            v.mission, v.remaining = command, list(command.waypoints)
            v.routine_queue, v.routine_done = [], False
            v.agent._behavior.max_speed = command.max_speed_kmh
            self._next_destination(v)
        elif isinstance(command, MissionHold):
            v = self.vehicles[command.vin]
            v.held, v.hold_left_s = True, command.duration_s
        elif isinstance(command, MissionResume):
            v = self.vehicles[command.vin]
            v.held, v.hold_left_s = False, None
        elif isinstance(command, AssistChoose):
            v = self.vehicles[command.vin]
            if v.assist_case == command.case_id:
                v.assist_case = None
                # day 3: translate the chosen option (wait / pass left / reroute) here

    def _next_destination(self, v: _Vehicle) -> None:
        v.agent.set_destination(self.transform(v.remaining[0]).location)

    async def start(self) -> None:
        settings = self.world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = self.fixed_delta_s
        self.world.apply_settings(settings)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.close_cameras()
        settings = self.world.get_settings()
        settings.synchronous_mode = False
        self.world.apply_settings(settings)

    def _tick_world(self):
        with self._tick_lock:
            return self.world.tick()

    async def _run(self) -> None:
        while True:
            for v in list(self.vehicles.values()):
                await self._step(v)
            await asyncio.to_thread(self._tick_world)
            self._since_status += self.fixed_delta_s
            if self._since_status >= 1.0:
                self._since_status = 0.0
                for v in self.vehicles.values():
                    await emit(self._handlers, self._status(v))

    async def _step(self, v: _Vehicle) -> None:
        if v.held and v.hold_left_s is not None:
            v.hold_left_s -= self.fixed_delta_s
            if v.hold_left_s <= 0:
                v.held, v.hold_left_s = False, None
        if v.mission is None or v.held or v.assist_case:
            v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
            return
        if v.routine_queue:
            await self._routine_step(v)
            return
        if v.agent.done():
            v.location = v.remaining.pop(0)
            if v.location == "test_track" and v.mission.routine and not v.routine_done:
                v.routine_done = True
                v.routine_queue = routine_steps(v.mission.routine, v.mission.routine_steps)
                if v.routine_queue:
                    v.step_left_s = v.routine_queue[0].duration_s
                    self._set_lights(v, v.routine_queue[0])
            if v.remaining:
                self._next_destination(v)
            else:
                mission, v.mission = v.mission, None
                v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
                await emit(self._handlers, MissionDone(
                    mission_id=mission.mission_id, vin=v.vin, location=v.location,
                ))
                return
        if await self._check_blocked(v):
            return
        v.actor.apply_control(v.agent.run_step())

    def store_artifact(self, uri: str, data: bytes) -> str:
        """Upload evidence and return the URI to record. Never raises."""
        return self.artifacts.put(uri, data, "image/jpeg")

    # ---------- flow 2: the driving system asks for help ----------

    def _obstacle_ahead(self, v: _Vehicle):
        """Nearest vehicle, walker or prop in front of the car, within obstacle_range_m."""
        here = v.actor.get_transform()
        forward = here.get_forward_vector()
        origin = here.location
        best, best_d = None, self.obstacle_range_m
        for actor in self.world.get_actors():
            if actor.id == v.actor.id:
                continue
            if not actor.type_id.startswith(("vehicle.", "walker.", "static.prop")):
                continue
            offset = actor.get_location() - origin
            ahead = offset.x * forward.x + offset.y * forward.y
            if ahead <= 0:                      # behind the car
                continue
            lateral = abs(offset.x * -forward.y + offset.y * forward.x)
            if lateral > 3.0:                   # not in this lane
                continue
            distance = origin.distance(actor.get_location())
            if distance < best_d:
                best, best_d = actor, distance
        return best, best_d

    def assist_clip(self, v: _Vehicle) -> bytes | None:
        """A forward view from behind the stopped car, showing what it is looking at."""
        here = v.actor.get_transform()
        forward, right = here.get_forward_vector(), here.get_right_vector()
        location = here.location + forward * -7.0 + right * 1.2
        location.z += 3.0
        pose = carla.Transform(location, carla.Rotation(yaw=here.rotation.yaw, pitch=-10.0))
        try:
            return self.capture(pose, EVIDENCE_WIDTH, EVIDENCE_HEIGHT)
        except Exception:                       # a missing frame must not block the request
            return None

    async def raise_assist(self, vin: str, case_id: str, reason: str, options: list[str],
                           clip_uri: str | None = None) -> None:
        """Make a car stop and ask for help, as the real driving system would."""
        v = self.vehicles[vin]
        v.assist_case = case_id
        v.assists_raised += 1
        v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
        clip = self.assist_clip(v)
        uri = clip_uri or f"gs://marshal/assist/{case_id}.jpg"
        if clip is not None:
            self.assist_clips[case_id] = clip
            uri = self.store_artifact(uri, clip)
        await emit(self._handlers, AssistRequest(
            case_id=case_id, vin=vin, location=v.location, reason=reason, options=options,
            clip_uri=uri,
        ))

    def assist_media(self, case_id: str) -> Media | None:
        """The frame captured when the car stopped, for the desk to show Gemini."""
        data = self.assist_clips.get(case_id)
        if data is None:
            return None
        return Media(mime="image/jpeg", data=data, label="forward view at the stop",
                     uri=f"gs://marshal/assist/{case_id}.jpg")  # recorded URI is on the request

    async def _check_blocked(self, v: _Vehicle) -> bool:
        """Raise assist.request once the agent has stalled mid-mission behind something."""
        if not self.auto_assist or v.assist_case or v.routine_queue:
            return False
        velocity = v.actor.get_velocity()
        speed_kmh = 3.6 * (velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2) ** 0.5
        if speed_kmh > self.blocked_speed_kmh:
            v.stalled_s = 0.0
            return False
        v.stalled_s += self.fixed_delta_s
        if v.stalled_s < self.blocked_after_s:
            return False
        obstacle, distance = self._obstacle_ahead(v)
        if obstacle is None:
            v.stalled_s = 0.0                   # stopped for its own reasons, not an obstacle
            return False
        kind = obstacle.type_id.split(".")[0]
        what = {"walker": "a person", "vehicle": "a vehicle", "static": "an object"}.get(
            kind, "an object")
        case_id = f"AC-{v.vin}-{v.assists_raised + 1}"
        await self.raise_assist(
            v.vin, case_id,
            f"unknown object in lane: {what} ({obstacle.type_id}) {distance:.1f} m ahead",
            self.assist_options,
        )
        v.stalled_s = 0.0
        return True

    def _set_lights(self, v: _Vehicle, step: RoutineStep | None) -> None:
        state = carla.VehicleLightState.NONE
        if step is not None and step.name in STEP_LIGHTS and step.name not in self.faults.get(v.vin, set()):
            state = getattr(carla.VehicleLightState, STEP_LIGHTS[step.name])
        v.actor.set_light_state(carla.VehicleLightState(state))

    async def _routine_step(self, v: _Vehicle) -> None:
        v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
        v.step_left_s -= self.fixed_delta_s
        if v.step_left_s > 0:
            return
        step = v.routine_queue.pop(0)
        # Telemetry is what the car's electronics report: healthy, even for a dark lamp.
        await emit(self._handlers, TrackStep(
            vin=v.vin, mission_id=v.mission.mission_id, routine=v.mission.routine,
            step=step.step, name=step.name, telemetry=dict(step.telemetry),
        ))
        nxt = v.routine_queue[0] if v.routine_queue else None
        self._set_lights(v, nxt)
        if nxt is not None:
            v.step_left_s = nxt.duration_s

    def capture(self, transform, width: int = 640, height: int = 360) -> bytes:
        """One RGB frame from a reusable camera at a world transform.

        Spawning and destroying a sensor per frame is what produced repeated
        "Invalid session: no stream available" errors and took the server down
        mid-run, so cameras are keyed by pose and kept for the life of the
        driving system. `close_cameras` releases them.
        """
        key = (round(transform.location.x, 2), round(transform.location.y, 2),
               round(transform.location.z, 2), round(transform.rotation.yaw, 1), width, height)
        entry = self._cameras.get(key)
        if entry is None:
            bp = self.world.get_blueprint_library().find("sensor.camera.rgb")
            bp.set_attribute("image_size_x", str(width))
            bp.set_attribute("image_size_y", str(height))
            frames: queue.Queue = queue.Queue(maxsize=4)

            def receive(image, q=frames) -> None:
                if q.full():                      # keep only recent frames
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                q.put(image)

            cam = self.world.spawn_actor(bp, transform)
            cam.listen(receive)
            entry = self._cameras[key] = (cam, frames)

        cam, frames = entry
        while not frames.empty():                 # discard anything queued
            try:
                frames.get_nowait()
            except queue.Empty:
                break

        # Draining is not enough. Frames already in flight arrive after the drain and still
        # show the previous world state, which made a dark lamp look lit because the frame
        # was of the previous, healthy car. Skip a couple before trusting one.
        skip = 2
        image = None
        if self.world.get_settings().synchronous_mode:
            for _ in range(8 + skip):
                self._tick_world()
                try:
                    candidate = frames.get(timeout=1.0)
                except queue.Empty:
                    continue
                if skip > 0:
                    skip -= 1
                    continue
                image = candidate
                break
        else:
            for _ in range(skip + 1):
                try:
                    candidate = frames.get(timeout=6.0)
                except queue.Empty:
                    break
                if skip > 0:
                    skip -= 1
                    continue
                image = candidate
        if image is None:
            raise RuntimeError("camera produced no frame after bounded warm-up")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.jpg"
            image.save_to_disk(str(path))
            return path.read_bytes()

    def close_cameras(self) -> None:
        """Release the reusable capture cameras."""
        for cam, _frames in self._cameras.values():
            try:
                cam.stop()
                cam.destroy()
            except Exception:                     # already gone with the world
                pass
        self._cameras.clear()

    def _status(self, v: _Vehicle) -> Status:
        loc = v.actor.get_location()
        vel = v.actor.get_velocity()
        speed_kmh = 3.6 * (vel.x ** 2 + vel.y ** 2 + vel.z ** 2) ** 0.5
        progress = 0.0
        if v.mission:
            progress = 1 - len(v.remaining) / max(len(v.mission.waypoints), 1)
        return Status(
            vin=v.vin, location=v.location, x=loc.x, y=loc.y, speed_kmh=speed_kmh,
            mission_id=v.mission.mission_id if v.mission else None, progress=progress,
            held=v.held, stopped_for_assist=v.assist_case is not None,
        )


# camera offsets around a map point: (forward m, right m, height m, yaw offset deg)
GATE_CAMERAS = {"front": (7.0, 0.0, 1.5, 180.0), "rear": (-7.0, 0.0, 1.5, 0.0),
                "left": (0.0, -5.0, 1.2, 90.0), "right": (0.0, 5.0, 1.2, -90.0)}

# A single rear three-quarter view cannot see front headlights at all, and Gemini
# correctly refused to confirm them from it. Each checklist step gets the view that
# shows the lamp it is about: front for headlights, rear for brake and reverse, a
# rear quarter for indicators (one repeater plus the tail cluster).
TRACK_CAMERAS = {
    "headlights": (8.0, 0.0, 1.3, 180.0),
    "indicators": (-7.0, 3.5, 1.3, -15.0),
    "brake_lights": (-8.0, 0.0, 1.2, 0.0),
    "reverse": (-8.0, 0.0, 1.2, 0.0),
}
TRACK_CAMERA_DEFAULT = (-8.0, 3.0, 1.6, -20.0)

# Gemini could not distinguish a lit headlight from a dark one at 640x360; the reverse lamp
# was invisible at that size too. Evidence frames are captured larger than the viewer's.
EVIDENCE_WIDTH, EVIDENCE_HEIGHT = 1280, 720


def _offset(ds: CarlaDrivingSystem, point: str, forward: float, right: float, z: float, yaw: float):
    base = ds.transform(point)
    fwd, rgt = base.get_forward_vector(), base.get_right_vector()
    loc = base.location + fwd * forward + rgt * right
    loc.z += z
    return carla.Transform(loc, carla.Rotation(yaw=base.rotation.yaw + yaw, pitch=-5.0))


class CarlaGateCamera:
    """Four RGB cameras around the inspection gate point."""

    def __init__(self, ds: CarlaDrivingSystem, point: str = "inspection_gate"):
        self.ds, self.point = ds, point

    def capture(self, vin: str, views: list[str] | None = None) -> list[Media]:
        out = []
        for view in GATE_VIEWS:
            if views is not None and view not in views:
                continue
            data = self.ds.capture(_offset(self.ds, self.point, *GATE_CAMERAS[view]),
                                   EVIDENCE_WIDTH, EVIDENCE_HEIGHT)
            uri = self.ds.store_artifact(f"gs://marshal/gate/{vin}/{view}.jpg", data)
            out.append(Media(mime="image/jpeg", data=data, label=f"{view} camera", uri=uri))
        return out


class CarlaTrackCamera:
    """One frame per checklist step, from the view that can actually see that lamp."""

    def __init__(self, ds: CarlaDrivingSystem, point: str = "test_track"):
        self.ds, self.point = ds, point

    def clip(self, vin: str, step: int, name: str) -> Media:
        placement = TRACK_CAMERAS.get(name, TRACK_CAMERA_DEFAULT)
        data = self.ds.capture(_offset(self.ds, self.point, *placement),
                               EVIDENCE_WIDTH, EVIDENCE_HEIGHT)
        uri = self.ds.store_artifact(f"gs://marshal/track/{vin}/step{step}_{name}.jpg", data)
        return Media(mime="image/jpeg", data=data, label=f"track camera, step {step} {name}",
                     uri=uri)
