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
import time
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
    onboard: object | None = None          # forward camera on the car; see assist_clip()
    onboard_frames: object | None = None   # queue.Queue of its frames


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
        # pose key -> (camera, frame queue, callback); see capture()/close_cameras()
        self._cameras: dict[tuple, tuple] = {}
        self.frame_timeout_s = 1.0            # how long to wait for one camera frame
        self._simulated_s = 0.0               # see realtime_ratio()
        self._sim_started_at: float | None = None
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

    def _attach_onboard(self, v: "_Vehicle") -> None:
        """Give the car a forward camera that streams for as long as it exists.

        The assist frame used to be taken by spawning a camera behind the car at the moment it
        stopped. That cannot work: the stop is detected inside the driving loop, so the loop is
        blocked in the call that is waiting for a frame only the loop could produce, and it failed
        every time with "camera produced no frame after bounded warm-up". The failure was
        swallowed, and the request went out carrying a gs:// URI nothing had been uploaded to,
        which surfaced later as a 403 from Vertex AI trying to read it.

        A camera on the car needs no pose chosen at the stop and no waiting: wherever it stops,
        the newest frame already delivered is the forward view from that car.
        """
        try:
            bp = self.world.get_blueprint_library().find("sensor.camera.rgb")
            bp.set_attribute("image_size_x", str(EVIDENCE_WIDTH))
            bp.set_attribute("image_size_y", str(EVIDENCE_HEIGHT))
            frames: queue.Queue = queue.Queue(maxsize=4)

            def receive(image, q=frames) -> None:
                if q.full():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                q.put(image)

            pose = carla.Transform(carla.Location(x=-6.0, z=3.0), carla.Rotation(pitch=-12.0))
            cam = self.world.spawn_actor(bp, pose, attach_to=v.actor)
            cam.listen(receive)
            v.onboard, v.onboard_frames = cam, frames
        except Exception as exc:
            # The camera is evidence, not part of driving. A car with no camera still has to be
            # able to leave the line; what it loses is the frame behind an assistance request.
            log.warning("no onboard camera for %s: %s: %s", v.vin, type(exc).__name__, exc)

    def spawn(self, vin: str, at: str = "line_end", colour: str | None = None,
              behind_m: float = 0.0) -> None:
        """Put a car on the map. behind_m queues it that far back along the point's heading,
        so several cars can wait at the line end without occupying the same pose."""
        bp = self.world.get_blueprint_library().find(self.vehicle_blueprint)
        if colour and bp.has_attribute("color"):
            bp.set_attribute("color", colour)            # "r,g,b"
        bp.set_attribute("role_name", vin)
        pose = self.transform(at)
        if behind_m:
            forward = pose.get_forward_vector()
            pose = carla.Transform(
                pose.location - forward * behind_m, pose.rotation)
        actor = self.world.spawn_actor(bp, pose)
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
        v = self.vehicles[vin] = _Vehicle(vin=vin, actor=actor, agent=agent, location=at)
        self._attach_onboard(v)

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

    def _owns_ticks(self) -> bool:
        """True when this call may tick the world itself.

        In synchronous mode the world must have exactly one source of ticks. The driving loop
        ticks once per frame; a capture that also ticked produced two concurrent sources, and
        the server answered with "Waited for 10 seconds on IO" then Signal 11.

        Note this alone does not make a capture work alongside a running loop: the loop is then
        the only ticker, and it is blocked waiting for the capture, so no frame ever arrives.
        Gate inspection has to run in a phase with no active driving loop.
        """
        return self.world.get_settings().synchronous_mode and self._task is None

    def _tick_world(self):
        with self._tick_lock:
            return self.world.tick()

    async def _run(self) -> None:
        self._sim_started_at = time.monotonic()
        while True:
            for v in list(self.vehicles.values()):
                await self._step(v)
            await asyncio.to_thread(self._tick_world)
            self._simulated_s += self.fixed_delta_s
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

    def position_xy(self, vin: str) -> tuple[float, float] | None:
        """Where a car is right now, in the same frame as reference_xy."""
        v = self.vehicles.get(vin)
        if v is None:
            return None
        location = v.actor.get_location()
        return float(location.x), float(location.y)

    def reference_xy(self, point: str) -> tuple[float, float]:
        """Where a named point is, in the same frame as Status.x/y: CARLA world coordinates."""
        location = self.transform(point).location
        return float(location.x), float(location.y)

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
        """The newest forward view from the camera on the car."""
        if v.onboard_frames is None:
            return None
        try:
            return self._latest_frame(v.onboard_frames)
        except Exception as exc:                # a missing frame must not block the request
            log.warning("assist clip capture failed for %s: %s: %s",
                        v.vin, type(exc).__name__, exc)
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
        key = self._camera_key(transform, width, height)
        entry = self._cameras.get(key) or self._make_camera(key, transform, width, height)
        cam, frames, receive = entry
        if not self._owns_ticks():
            # The driving loop is ticking, and this call is on the loop's own thread: track
            # evidence is taken from inside the handler the loop is waiting on. Waiting for a
            # newly delivered frame therefore deadlocks - the only thing that could produce one
            # is the loop, which is blocked here - and that is what "camera produced no frame
            # after bounded warm-up" was. So take the freshest frame the camera has already
            # delivered, which is what day 2's save_latest does and why day 2 never hung.
            #
            # The cost is honest: that frame is up to a few ticks old, so it cannot be used to
            # tell a lamp that just changed from one that did not. Flow 4 does not rely on it
            # for that - it compares against a reference frame - and a step that needs an exact
            # moment needs a clip, which is still open.
            return self._latest_frame(frames)
        while not frames.empty():                 # discard anything queued
            try:
                frames.get_nowait()
            except queue.Empty:
                break

        # Cameras stay subscribed. Stopping an idle camera and restarting it per capture was an
        # attempt to spare the server, but it cannot work while the driving loop is running: the
        # loop is the only ticker, a camera that subscribes mid-capture has no tick of its own to
        # wait for, and the capture failed with "camera produced no frame after bounded warm-up".
        # It also wedged the gate: cycling the subscription logged "attempting to unsubscribe
        # from stream but sensor wasn't listening" and the next destroy timed out.
        #
        # What actually overloaded the server was four gate cameras streaming at full resolution
        # at once. The gate no longer uses this path - it moves a single camera - so what streams
        # here is the track's pair, which is the configuration day 2 has always run.
        # Draining is not enough. Frames already in flight arrive after the drain and still
        # show the previous world state, which made a dark lamp look lit because the frame
        # was of the previous, healthy car. Skip a couple before trusting one.
        skip = 2
        image = None
        if self._owns_ticks():
            for _ in range(8 + skip):
                self._tick_world()
                try:
                    candidate = frames.get(timeout=self.frame_timeout_s)
                except queue.Empty:
                    continue
                if skip > 0:
                    skip -= 1
                    continue
                image = candidate
                break
        else:
            # the driving loop (or the server in async mode) produces the frames
            for _ in range(skip + 1):
                try:
                    candidate = frames.get(timeout=self.frame_timeout_s * 6)
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

    def capture_moving(self, transform, width: int | None = None, height: int | None = None,
                       settle_frames: int = 4) -> bytes:
        """Capture from a single camera that is moved to this pose.

        This follows day 2's gate phase exactly, because that is the only camera pattern that has
        never crashed the server: one sensor, subscribed once and left subscribed, moved between
        poses, and each capture reads a fresh frame rather than driving the sensor.

        Cycling listen()/stop() per view - which capture() does, and which is fine for a sensor
        used once - wedged the server here: it logged "attempting to unsubscribe from stream but
        sensor wasn't listening" and the next destroy timed out. Four cameras streaming at once
        was the original problem; one is what day 2 has always done.
        """
        width = EVIDENCE_WIDTH if width is None else width
        height = EVIDENCE_HEIGHT if height is None else height
        key = ("movable", width, height)
        entry = self._cameras.get(key)
        if entry is None:
            entry = self._make_movable(key, transform, width, height)
        cam, frames, _receive = entry

        cam.set_transform(transform)
        # Frames already in flight show the previous pose, so wait for several fresh ones and
        # keep the last.
        image = None
        deadline = time.monotonic() + max(2.0, self.frame_timeout_s * (settle_frames + 4))
        seen = 0
        while time.monotonic() < deadline:
            if self._owns_ticks():
                self._tick_world()
            try:
                candidate = frames.get(timeout=self.frame_timeout_s)
            except queue.Empty:
                continue
            seen += 1
            image = candidate
            if seen > settle_frames:
                break
        if image is None:
            raise RuntimeError("camera produced no frame after moving to the requested pose")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.jpg"
            image.save_to_disk(str(path))
            return path.read_bytes()

    def _latest_frame(self, frames: "queue.Queue") -> bytes:
        """The newest frame already delivered, waiting only if none has been."""
        image = None
        while True:                               # drain to the newest
            try:
                image = frames.get_nowait()
            except queue.Empty:
                break
        if image is None:                         # nothing delivered yet: give it a moment
            try:
                image = frames.get(timeout=self.frame_timeout_s * 6)
            except queue.Empty:
                raise RuntimeError("camera produced no frame after bounded warm-up") from None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.jpg"
            image.save_to_disk(str(path))
            return path.read_bytes()

    def _make_camera(self, key, transform, width: int, height: int):
        """Spawn a fixed camera and subscribe it, for the life of the driving system.

        prewarm_camera used to spawn without subscribing while capture() spawned and subscribed,
        because each built the sensor itself. A prewarmed camera was therefore silent, so the
        track phase - which prewarms before starting the loop, exactly as it is meant to - got
        "camera produced no frame after bounded warm-up". One creation path, so the two cannot
        drift apart again.
        """
        bp = self.world.get_blueprint_library().find("sensor.camera.rgb")
        bp.set_attribute("image_size_x", str(width))
        bp.set_attribute("image_size_y", str(height))
        frames: queue.Queue = queue.Queue(maxsize=4)

        def receive(image, q=frames) -> None:
            if q.full():                          # keep only recent frames
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
            q.put(image)

        cam = self.world.spawn_actor(bp, transform)
        cam.listen(receive)
        entry = self._cameras[key] = (cam, frames, receive)
        return entry

    def _make_movable(self, key, transform, width: int, height: int):
        """Create the movable camera and subscribe it once, for the life of the run."""
        bp = self.world.get_blueprint_library().find("sensor.camera.rgb")
        bp.set_attribute("image_size_x", str(width))
        bp.set_attribute("image_size_y", str(height))
        frames: queue.Queue = queue.Queue(maxsize=8)

        def receive(image, q=frames) -> None:
            if q.full():
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
            q.put(image)

        cam = self.world.spawn_actor(bp, transform)
        cam.listen(receive)                   # subscribed once; never stopped mid-run
        entry = self._cameras[key] = (cam, frames, receive)
        return entry

    def prewarm_camera(self, transform, width: int | None = None,
                       height: int | None = None, movable: bool = False) -> None:
        """Create the sensor for this pose now, without taking a frame.

        Day 2 is stable because every camera exists before `start()`; the app path created
        them lazily on the first capture, which happens while the loop is ticking, and the
        server segfaults there (Signal 11). Call this before `start()` for each pose a run
        will use.
        """
        # resolved here rather than as defaults: EVIDENCE_* is defined below this class
        width = EVIDENCE_WIDTH if width is None else width
        height = EVIDENCE_HEIGHT if height is None else height
        if movable:
            key = ("movable", width, height)
            if key not in self._cameras:
                self._make_movable(key, transform, width, height)
            return
        key = self._camera_key(transform, width, height)
        if key not in self._cameras:
            self._make_camera(key, transform, width, height)

    @staticmethod
    def _camera_key(transform, width: int, height: int) -> tuple:
        return (round(transform.location.x, 2), round(transform.location.y, 2),
                round(transform.location.z, 2), round(transform.rotation.yaw, 1), width, height)

    def realtime_ratio(self) -> float | None:
        """Simulated seconds per real second. Below 1.0 means the box cannot keep up."""
        if self._sim_started_at is None or not self._simulated_s:
            return None
        elapsed = time.monotonic() - self._sim_started_at
        return round(self._simulated_s / elapsed, 2) if elapsed > 0 else None

    def close_cameras(self) -> None:
        """Release the reusable capture cameras."""
        for cam, _frames, _receive in self._cameras.values():
            try:
                cam.stop()                        # a no-op if it is already idle
                cam.destroy()
            except Exception:                     # already gone with the world
                pass
        self._cameras.clear()
        # The cars' own cameras too. They are attached, so the server drops them with the car,
        # but releasing them here keeps "sensor object went out of the scope but the sensor is
        # still alive" out of the logs and makes cleanup one thing rather than two.
        for v in self.vehicles.values():
            if v.onboard is not None:
                try:
                    v.onboard.stop()
                    v.onboard.destroy()
                except Exception as exc:
                    log.warning("could not release the onboard camera for %s: %s", v.vin, exc)
                v.onboard, v.onboard_frames = None, None

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
# Two fixed views, front and rear, and each step is judged from whichever can see its lamp.
# A camera per step meant four 1280x720 sensors streaming at once, which is what overloaded the
# server, and gave nothing extra: these are the poses day 2 has always used.
TRACK_VIEWS = {
    "front": (8.0, 0.0, 1.3, 180.0),
    "rear": (-8.0, 0.0, 1.2, 0.0),
}
TRACK_CAMERAS = {
    "headlights": TRACK_VIEWS["front"],
    "indicators": TRACK_VIEWS["rear"],
    "brake_lights": TRACK_VIEWS["rear"],
    "reverse": TRACK_VIEWS["rear"],
}
# Anything without a named view is judged from the rear, so no third sensor streams.
TRACK_CAMERA_DEFAULT = TRACK_VIEWS["rear"]

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

    def prewarm(self, views: list[str] | None = None) -> None:
        """Create the single movable gate sensor before the driving loop starts."""
        first = next(iter(GATE_VIEWS if views is None else views))
        self.ds.prewarm_camera(_offset(self.ds, self.point, *GATE_CAMERAS[first]),
                               EVIDENCE_WIDTH, EVIDENCE_HEIGHT, movable=True)

    def capture(self, vin: str, views: list[str] | None = None) -> list[Media]:
        out = []
        for view in GATE_VIEWS:
            if views is not None and view not in views:
                continue
            data = self.ds.capture_moving(_offset(self.ds, self.point, *GATE_CAMERAS[view]),
                                          EVIDENCE_WIDTH, EVIDENCE_HEIGHT)
            uri = self.ds.store_artifact(f"gs://marshal/gate/{vin}/{view}.jpg", data)
            out.append(Media(mime="image/jpeg", data=data, label=f"{view} camera", uri=uri))
        return out


class CarlaTrackCamera:
    """One frame per checklist step, from the view that can actually see that lamp."""

    def __init__(self, ds: CarlaDrivingSystem, point: str = "test_track"):
        self.ds, self.point = ds, point

    def prewarm(self) -> None:
        """Create every per-step track sensor before the driving loop starts."""
        for placement in list(TRACK_CAMERAS.values()) + [TRACK_CAMERA_DEFAULT]:
            self.ds.prewarm_camera(_offset(self.ds, self.point, *placement),
                                   EVIDENCE_WIDTH, EVIDENCE_HEIGHT)

    def clip(self, vin: str, step: int, name: str) -> Media:
        placement = TRACK_CAMERAS.get(name, TRACK_CAMERA_DEFAULT)
        data = self.ds.capture(_offset(self.ds, self.point, *placement),
                               EVIDENCE_WIDTH, EVIDENCE_HEIGHT)
        uri = self.ds.store_artifact(f"gs://marshal/track/{vin}/step{step}_{name}.jpg", data)
        return Media(mime="image/jpeg", data=data, label=f"track camera, step {step} {name}",
                     uri=uri)
