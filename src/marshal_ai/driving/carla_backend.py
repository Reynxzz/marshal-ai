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
import math
import queue
import threading
from collections import deque
import tempfile
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


# The design asks for a 3 s clip of each track step and a 10 s clip before a stop. Frames are held
# for those windows and sampled: what has to be read is a change across the window, not sixty
# consecutive frames, and keeping every frame of ten seconds would be some 700 MB per camera.
#
# The clip is taken when a step reports in, so it looks backwards, and the window has to hold the
# step and not what follows it. Widened to 12 s it caught the car driving away afterwards - by the
# last frame the car was a speck and its lamps were not resolvable - so it stays close to the
# design's 3 s. The car is stationary for the length of a step, so a fixed camera keeps it in frame.
CLIP_WINDOW_S, CLIP_FRAMES = 4.0, 8

# How often the indicators step changes side. The step lasts 10 s and originally switched once, at
# its midpoint, so a clip of the last seconds showed one side steadily lit and the step failed a
# healthy car. Alternating faster than the clip window means any clip covers a change of side.
INDICATOR_PERIOD_S = 1.2

# Evidence views bolted to the car: (x, z, pitch, yaw). "rear" sits behind looking forward, so the
# car's back fills the frame; "front" sits ahead looking back at its face.
# A narrow field of view, because the lamps are what has to be read. At the default 90 degrees the
# car sat in the middle of a wide road scene and an indicator was a handful of pixels: Gemini could
# not tell which side was lit, and said so at confidence 1.0. Narrowed, the car's end fills the frame.
EVIDENCE_FOV = 38.0
ONBOARD_VIEWS = {
    "rear": (-7.0, 1.6, -5.0, 0.0),
    "front": (7.5, 1.6, -5.0, 180.0),
}
ASSIST_WINDOW_S, ASSIST_FRAMES = 10.0, 8


class _FrameBuffer:
    """The recent past of one camera, sampled over time.

    A clip cannot be taken by asking the camera for frames when the moment arrives: the stop or the
    routine step is detected inside the driving loop, so the loop is blocked in that very call and
    no further frame can arrive. The frames have to be there already, which they are - the cameras
    stream continuously - so this keeps them.

    It cannot keep all of them. A CARLA frame at 1280x720 is raw BGRA, about 3.7 MB, and the server
    produces 20 a second: ten seconds would be some 700 MB per camera. So frames are kept at an
    interval spread across the window, capped in number, which is also what the judgement needs - a
    blinker cycle is read from a handful of frames across it, not from sixty consecutive ones.

    The newest frame is kept whatever the sampling, because a single-frame capture must reflect the
    world now rather than the last sample.
    """

    def __init__(self, window_s: float = 3.0, max_frames: int = 12, clock=time.monotonic,
                 encode=None):
        self.window_s = window_s
        self.max_frames = max_frames
        self._interval_s = window_s / max_frames if max_frames else window_s
        self._samples: deque = deque(maxlen=max_frames)
        self._latest = None
        self._last_kept_at = 0.0
        self._clock = clock
        # Kept samples are encoded as they arrive rather than held as CARLA frames. A frame object
        # belongs to its sensor's stream and is not something to keep for seconds: holding a few
        # for later encoding took the server down with a 20 s timeout. Encoding also turns 3.7 MB
        # of raw BGRA into about 0.5 MB of JPEG, so a window costs megabytes rather than hundreds.
        # It happens once per sample, a few times a second, not on every frame.
        self._encode = encode
        self._lock = threading.Lock()

    def append(self, image) -> None:
        now = self._clock()
        with self._lock:
            self._latest = image        # one live handle, replaced many times a second
            keep = not self._samples or now - self._last_kept_at >= self._interval_s
        if not keep:
            return
        sample = self._encode(image) if self._encode is not None else image
        with self._lock:
            self._samples.append((now, sample))
            self._last_kept_at = now

    def latest(self):
        with self._lock:
            return self._latest

    def recent(self, count: int) -> list:
        """Up to `count` frames spread across the window, oldest first."""
        with self._lock:
            samples = list(self._samples)
        if not samples:
            return []
        if len(samples) <= count:
            return samples
        # even spread, always including the oldest and newest held
        step = (len(samples) - 1) / (count - 1) if count > 1 else 0
        picked = [samples[min(len(samples) - 1, round(i * step))] for i in range(count)]
        seen, out = set(), []
        for at, image in picked:                  # rounding can repeat an index
            if at not in seen:
                seen.add(at)
                out.append((at, image))
        return out

    def empty(self) -> bool:
        with self._lock:
            return self._latest is None


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
    creep_s: float = 0.0                   # time spent creeping onto a precise stop; see _creep
    manoeuvre: dict | None = None          # state of a routine step being driven; see _manoeuvre
    assists_raised: int = 0
    onboard: object | None = None          # forward camera on the car; see assist_clip()
    onboard_frames: object | None = None   # _FrameBuffer of its frames
    # view name -> (sensor, _FrameBuffer). Track evidence rides with the car; see _attach_evidence.
    evidence: dict = field(default_factory=dict)


# The options the driving system offers when it stops. The desk may only choose among these;
# it never invents a path. "wait" first so a rule-bound desk always has a safe choice.
ASSIST_OPTIONS = ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"]

# Simulated seconds between frames for every Marshal camera (CARLA's sensor_tick). Measured on the
# T4 with three cameras per car: at the default of rendering every tick, simulated time ran at 1.26x
# real time with one car, 0.47x with three and 0.28x with five; at 0.2 s, 1.54x with three and 0.98x
# with five, with no crash either way. Nothing needs more: a clip samples every 0.5 s, the indicator
# changes side every 1.2 s, and a still is the newest frame.
CAMERA_TICK_S = 0.2

# Points where a car has to stop on the mark, because cameras are aimed at it. BehaviorAgent counts
# itself as arrived once within about 3-4 m of a destination, which is fine for a yard slot and wrong
# for the gate: the car stopped short, the rear camera ended up over its roof, the side camera caught
# only its front half, and Gemini reported a "panel gap" at 0.9 on a car it could barely see. Scene 1
# never showed it because it spawns the car on the mark.
PRECISE_STOPS = {"inspection_gate"}
# Points a car drives through instead of stopping at: the design's gate photographs each car rolling
# through at walking speed (flow 3, 5 km/h). The car holds its mission speed onto the mark, arrives
# - and is photographed - as its centre crosses it, and is not braked: the gate's verdict and the
# next mission come while the simulation is paused for them, so the car carries straight on to the
# track. Only a car with nowhere to go next (a REVIEW) slows to a halt just past the gate.
# Off (drive_through_gate=False), the gate is a precise stop, as before.
DRIVE_THROUGH = {"inspection_gate"}
DRIVE_THROUGH_DEFAULT_KMH = 5.0

# Routine steps the car physically performs, measured instead of reported (design, flow 4). Until
# these, the car stood braked through the whole routine and the reverse and brake-test telemetry were
# constants. The track is short - 20 m from the routine's start to the report point on Town05 - so
# the brake test's run-up is capped, and the car is re-routed from where it ends up.
MANOEUVRES = {"reverse", "brake_test"}
REVERSE_TARGET_M = 3.0
REVERSE_CLEARANCE_M = 4.0          # reverse no further while anything is closer than this behind
BRAKE_TEST_KMH = 20.0
BRAKE_TEST_RUN_UP_MAX_M = 12.0     # brake here even if 20 km/h was not reached; reported as such
MANOEUVRE_MAX_S = 25.0
STOP_TOLERANCE_M = 0.4
CREEP_MAX_S = 30.0                 # give up creeping after this long, rather than wait forever
# A pull_over point (plant_map.yaml) is off the driving lanes - a bay or yard slot on the shoulder -
# and BehaviorAgent only routes on driving lanes. So the car is routed to the lane this far before
# the spot, then steers onto it. 12 m is enough to move one lane over at walking pace.
PULL_OVER_RUN_M = 12.0
PULL_OVER_AIM_M = 4.0              # steer for this far beyond the spot, along it, to arrive straight
SETTLE_MAX_M = 3.0                 # a car this close to its slot or bay is squared up on it (_square_up)

# Every sensor Marshal spawns carries this role_name, so leftovers can be found and cleared. Runs used
# to leave cameras alive in the world - 28 built up in one session, each rendering every frame on the
# GPU - and nothing could tell Marshal's apart from the viewer's or a replay's. The lab already tags
# its actors the same way ("marshal-lab").
SENSOR_ROLE = "marshal-sensor"

STEP_LIGHTS = {
    "headlights": "LowBeam", "indicators": "LeftBlinker", "brake_lights": "Brake", "reverse": "Reverse",
}

# The indicators step is "left, then right". Only LeftBlinker was ever set, so the right indicator
# was never energised and the step could not be performed, let alone confirmed - the camera check
# was failing a car for something the simulation had not done. The side switches halfway through.
#
# CARLA renders a blinker light state as steadily lit rather than flashing, so what a camera can
# confirm here is that the correct side is lit and that it changes sides. The flash rate itself is
# not simulated, and stays out of scope as horn and wipers do.
INDICATOR_SIDES = ("LeftBlinker", "RightBlinker")


def _destroy_quietly(actor) -> None:
    """Destroy an actor that may already be gone, and that this process may not have created.

    No stop() first: these are found in the world, not held, and stopping a sensor this process
    never subscribed to only logs "attempting to unsubscribe from stream but sensor wasn't
    listening" - thirteen of them at the start of a run. Sensors this process does hold are stopped
    by release_car_sensors and close_cameras before they come here.
    """
    try:
        actor.destroy()
    except Exception as exc:
        log.warning("could not destroy %s %s: %s", getattr(actor, "type_id", "actor"),
                    getattr(actor, "id", "?"), exc)


class CarlaDrivingSystem:
    def __init__(self, plant_map: PlantMap, host: str = "localhost", port: int = 2000,
                 fixed_delta_s: float = 0.05, behavior: str = "cautious",
                 vehicle_blueprint: str = "vehicle.lincoln.mkz_2020",
                 timeout_s: float = 20.0, expected_version: str = "0.9.16",
                 auto_assist: bool = True, blocked_after_s: float = 5.0,
                 blocked_speed_kmh: float = 0.6, obstacle_range_m: float = 14.0,
                 assist_options: list[str] | None = None, sweep_stale_sensors: bool = True,
                 camera_tick_s: float | None = CAMERA_TICK_S, drive_through_gate: bool = True):
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
        self.assist_clips: dict[str, list[bytes]] = {}
        self.blocked_after_s = blocked_after_s
        self.blocked_speed_kmh = blocked_speed_kmh
        self.obstacle_range_m = obstacle_range_m
        self.assist_options = list(assist_options) if assist_options else list(ASSIST_OPTIONS)
        self.auto_assist = auto_assist
        # Reusable capture cameras, keyed by pose; see capture()/close_cameras().
        # pose key -> (camera, frame queue, callback); see capture()/close_cameras()
        self._cameras: dict[tuple, tuple] = {}
        self.frame_timeout_s = 1.0            # how long to wait for one camera frame
        # Simulated seconds between frames for every Marshal camera; None renders on every tick.
        # See _camera_blueprint.
        self.camera_tick_s = camera_tick_s
        self.drive_through_gate = drive_through_gate
        # vin -> km/h at the moment it crossed a drive-through point, i.e. when it was photographed
        self.passes: dict[str, float] = {}
        self._simulated_s = 0.0               # see realtime_ratio()
        self._sim_started_at: float | None = None
        # Replaced by the runtime when MARSHAL_ARTIFACTS=gcs, so the gs:// URI a record
        # carries points at the actual photo behind the verdict.
        self.artifacts: Artifacts = NoArtifacts()
        if sweep_stale_sensors:
            self.sweep_sensors()

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
        """Give the car the cameras its evidence comes from, for as long as it exists.

        Fixed cameras at the track cannot do this job. The car moves while it performs the routine,
        so a camera bolted to the map loses it: by the end of a clip the car was a speck twenty
        metres away and no lamp on it was resolvable, which failed the indicators step on a healthy
        car. A camera attached to the car keeps it framed wherever it goes.

        Three views: one ahead of the car for what it is about to hit, and one at each end for its
        lamps. All of them stream, so the recent past is always there - which matters because the
        moment a clip is wanted, the driving loop is blocked in the call asking for it.
        """
        v.onboard, v.onboard_frames = self._attach_camera(
            v, x=-6.0, z=3.0, pitch=-12.0, yaw=0.0,
            window_s=ASSIST_WINDOW_S, frames=ASSIST_FRAMES)
        for view, (x, z, pitch, yaw) in ONBOARD_VIEWS.items():
            cam, buffer = self._attach_camera(v, x=x, z=z, pitch=pitch, yaw=yaw,
                                              window_s=CLIP_WINDOW_S, frames=CLIP_FRAMES,
                                              fov=EVIDENCE_FOV)
            if cam is not None:
                v.evidence[view] = (cam, buffer)

    def _attach_camera(self, v: "_Vehicle", x: float, z: float, pitch: float, yaw: float,
                       window_s: float, frames: int, fov: float | None = None):
        """One camera bolted to the car, subscribed for the life of the car."""
        try:
            bp = self._camera_blueprint(EVIDENCE_WIDTH, EVIDENCE_HEIGHT, fov)
            buffer = _FrameBuffer(window_s=window_s, max_frames=frames, encode=self._encode)

            def receive(image, into=buffer) -> None:
                into.append(image)

            pose = carla.Transform(carla.Location(x=x, z=z), carla.Rotation(pitch=pitch, yaw=yaw))
            cam = self.world.spawn_actor(bp, pose, attach_to=v.actor)
            cam.listen(receive)
            return cam, buffer
        except Exception as exc:
            # The cameras are evidence, not part of driving. A car with none still has to be able
            # to leave the line; what it loses is the frame behind a verdict.
            log.warning("no camera at x=%s yaw=%s for %s: %s: %s",
                        x, yaw, v.vin, type(exc).__name__, exc)
            return None, None

    def _camera_blueprint(self, width: int, height: int, fov: float | None = None):
        """An RGB camera blueprint, tagged so Marshal can find its own sensors later."""
        bp = self.world.get_blueprint_library().find("sensor.camera.rgb")
        bp.set_attribute("image_size_x", str(width))
        bp.set_attribute("image_size_y", str(height))
        if fov is not None and bp.has_attribute("fov"):
            bp.set_attribute("fov", str(fov))
        if bp.has_attribute("role_name"):
            bp.set_attribute("role_name", SENSOR_ROLE)
        # Every car carries three 1280x720 cameras, and by default each renders on every tick - 20
        # times a simulated second - though nothing reads them that often: a clip keeps a sample
        # every half second and a still takes the newest frame. sensor_tick is how often CARLA
        # renders the camera, so with several cars this is the difference between 3N full-rate
        # streams and 3N slow ones.
        tick = getattr(self, "camera_tick_s", None)
        if tick and bp.has_attribute("sensor_tick"):
            bp.set_attribute("sensor_tick", str(tick))
        return bp

    def release_car_sensors(self, v: "_Vehicle") -> None:
        """Stop and destroy the cameras attached to one car.

        CARLA does not destroy a sensor when the car it is attached to is destroyed: the sensor stays
        in the world, still rendering. Each car carries three, so every car a run removed without this
        left three cameras behind.
        """
        cams = [v.onboard] + [cam for cam, _buffer in v.evidence.values()]
        for cam in cams:
            if cam is None:
                continue
            try:
                cam.stop()
                cam.destroy()
            except Exception as exc:              # already gone with the world
                log.warning("could not release a camera on %s: %s", v.vin, exc)
        v.onboard, v.onboard_frames = None, None
        v.evidence.clear()

    def despawn(self, vin: str) -> None:
        """Remove a car and everything attached to it, whether or not this process spawned it.

        A car left by an earlier process is found by its role_name (the VIN), and its sensors by
        being attached to it, so a new phase can clear the previous phase's car completely.
        """
        v = self.vehicles.pop(vin, None)
        if v is not None:
            self.release_car_sensors(v)
        actors = self.world.get_actors()
        removed = False
        for car in actors.filter("vehicle.*"):
            if car.attributes.get("role_name") != vin:
                continue
            for sensor in actors.filter("sensor.*"):
                if sensor.parent is not None and sensor.parent.id == car.id:
                    _destroy_quietly(sensor)
            _destroy_quietly(car)
            removed = True
        if removed:
            # A destroyed actor leaves the world on the server's next frame, not when destroy()
            # returns. Respawning the car at the same pose straight away failed with "Spawn failed
            # because of collision at spawn position": it collided with itself.
            self._settle()

    def _settle(self) -> None:
        """Let the server apply what was just changed, by letting one frame pass."""
        try:
            if self.world.get_settings().synchronous_mode:
                if self._owns_ticks():
                    self._tick_world()
            else:
                self.world.wait_for_tick(seconds=10.0)
        except Exception as exc:                  # a settle that fails must not hide the cause
            log.warning("could not wait for the server to apply a change: %s", exc)

    def sweep_sensors(self) -> int:
        """Destroy Marshal sensors left in the world by earlier processes. Returns how many.

        Called when a backend connects, so each run starts from a world with none of Marshal's
        cameras in it, however the previous run ended - including a phase that deliberately skipped
        teardown, or a process that was killed. Only sensors tagged SENSOR_ROLE are touched: the
        viewer's camera and a replay's are left alone.

        Run one Marshal process against a server at a time. A second process connecting would clear
        the first one's cameras.
        """
        stale = [a for a in self.world.get_actors().filter("sensor.*")
                 if a.attributes.get("role_name") == SENSOR_ROLE]
        for sensor in stale:
            _destroy_quietly(sensor)
        if stale:
            log.info("cleared %d Marshal sensor(s) left by an earlier run", len(stale))
        return len(stale)

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
            self._ignore_parked(agent)
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

    def _precise(self, point: str) -> bool:
        spec = self.map.points[point].carla or {}
        return point in PRECISE_STOPS or bool(spec.get("pull_over"))

    def _next_destination(self, v: _Vehicle) -> None:
        point = v.remaining[0]
        target = self.transform(point).location
        if (self.map.points[point].carla or {}).get("pull_over"):
            # Off the driving lanes: route to the lane beside the spot, a run-up before it.
            wp = self.world.get_map().get_waypoint(target, project_to_road=True,
                                                   lane_type=carla.LaneType.Driving)
            back = wp.previous(PULL_OVER_RUN_M) if wp is not None else []
            target = (back[0] if back else wp).transform.location if wp is not None else target
        v.agent.set_destination(target)

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
            through = self._drives_through(v.remaining[0])
            if through:
                if not self._pass_through(v):
                    return                      # rolling onto the mark at gate speed
            elif self._precise(v.remaining[0]) and not self._creep(v):
                return                          # still edging onto the mark
            v.creep_s = 0.0
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
                if not through:
                    v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
                # A drive-through car keeps rolling: if the handlers give it its next mission, as
                # a gate PASS does, it never stops; if not, the next step brakes it.
                await emit(self._handlers, MissionDone(
                    mission_id=mission.mission_id, vin=v.vin, location=v.location,
                ))
                return
        if await self._check_blocked(v):
            return
        v.actor.apply_control(v.agent.run_step())

    def _creep(self, v: _Vehicle) -> bool:
        """Edge the car onto its stop mark at walking pace. True once it is there.

        Measured along the car's heading, so a car that has rolled past the mark counts as there.
        It brakes for anything in front, and gives up after CREEP_MAX_S rather than hold up the
        plant if something it cannot see is in the way.
        """
        point = v.remaining[0]
        target = self.transform(point).location
        here = v.actor.get_transform()
        forward = here.get_forward_vector()
        dx, dy = target.x - here.location.x, target.y - here.location.y
        along = dx * forward.x + dy * forward.y
        pulling_over = bool((self.map.points[point].carla or {}).get("pull_over"))
        v.creep_s += self.fixed_delta_s
        close = math.hypot(dx, dy) <= 1.0
        if along <= STOP_TOLERANCE_M or close or v.creep_s > CREEP_MAX_S:
            v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
            if v.creep_s > CREEP_MAX_S:
                log.warning("%s stopped %.1f m short of %s", v.vin, along, v.remaining[0])
            elif pulling_over:
                self._square_up(v, point)
            return True
        obstacle, distance = self._obstacle_ahead(v)
        if obstacle is not None and distance < along + 3.0:
            v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
            return False
        heading = math.atan2(forward.y, forward.x)
        aim_x, aim_y = dx, dy
        if pulling_over:
            # Steer for a point beyond the spot, along it, as a driver lines up with a bay. Steered
            # at the spot itself the car arrived still angled across it (24 degrees in a yard slot)
            # with its tail in the lane beside, where the next car to pass stopped against it.
            spot = self.transform(point).get_forward_vector()
            aim_x, aim_y = dx + spot.x * PULL_OVER_AIM_M, dy + spot.y * PULL_OVER_AIM_M
        bearing = math.atan2(aim_y, aim_x)
        error = (bearing - heading + math.pi) % (2 * math.pi) - math.pi
        velocity = v.actor.get_velocity()
        speed = math.hypot(velocity.x, velocity.y)
        # Pulling over is a lane's width sideways in a short run, so it may steer harder than the
        # straight creep onto the gate mark, which only corrects a little.
        limit = 0.6 if pulling_over else 0.3
        v.actor.apply_control(carla.VehicleControl(
            throttle=0.3 if speed < 1.0 else 0.0,          # about 3.6 km/h
            steer=max(-limit, min(limit, 1.5 * error)),
            brake=0.0))
        return False

    def _drives_through(self, point: str) -> bool:
        return getattr(self, "drive_through_gate", False) and point in DRIVE_THROUGH

    def _pass_through(self, v: _Vehicle) -> bool:
        """Roll onto a drive-through mark at the mission's speed. True as the car's centre crosses it.

        BehaviorAgent reports done 3-4 m out, so these last metres are driven here: hold the lane by
        aiming past the mark along the point's heading, hold the mission speed, brake only for
        something in front. The speed at the crossing is kept in self.passes, since it is the claim.
        """
        point = v.remaining[0]
        mark = self.transform(point)
        here = v.actor.get_transform()
        forward = here.get_forward_vector()
        dx, dy = mark.location.x - here.location.x, mark.location.y - here.location.y
        along = dx * forward.x + dy * forward.y
        velocity = v.actor.get_velocity()
        kmh = 3.6 * math.hypot(velocity.x, velocity.y)
        if along <= 0.0:
            self.passes[v.vin] = round(kmh, 1)
            log.info("%s drove through %s at %.1f km/h", v.vin, point, kmh)
            return True
        v.creep_s += self.fixed_delta_s
        if v.creep_s > CREEP_MAX_S:
            log.warning("%s did not reach %s in %.0f s; counted as through", v.vin, point, CREEP_MAX_S)
            self.passes[v.vin] = round(kmh, 1)
            return True
        obstacle, distance = self._obstacle_ahead(v)
        if obstacle is not None and distance < along + 4.0:
            v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
            return False
        lane = mark.get_forward_vector()
        aim_x, aim_y = dx + lane.x * 6.0, dy + lane.y * 6.0
        error = ((math.atan2(aim_y, aim_x) - math.atan2(forward.y, forward.x) + math.pi)
                 % (2 * math.pi) - math.pi)
        target = (v.mission.max_speed_kmh if v.mission and v.mission.max_speed_kmh
                  else DRIVE_THROUGH_DEFAULT_KMH)
        throttle = 0.4 if kmh < target - 0.5 else 0.0
        brake = 0.3 if kmh > target + 1.5 else 0.0
        v.actor.apply_control(carla.VehicleControl(
            throttle=throttle, brake=brake, steer=max(-0.3, min(0.3, 1.5 * error))))
        return False

    def _square_up(self, v: _Vehicle, point: str) -> None:
        """Square a car up in its slot or bay once it has crept onto it.

        A real plant driving system parks precisely; this stand-in steers BehaviorAgent's car off
        the lane by hand and arrives a little angled. Even at 17 degrees a car in yard slot Y2 kept
        its tail in the lane beside, and the next car creeping to Y4 stopped against it, 15 m short.
        So the last metre or two is set, not driven: the car is placed on the spot, straight.
        """
        spot = self.transform(point)
        here = v.actor.get_transform()
        if here.location.distance(spot.location) > SETTLE_MAX_M:
            return
        v.actor.set_transform(carla.Transform(
            carla.Location(spot.location.x, spot.location.y, here.location.z), spot.rotation))
        v.actor.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))

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

    def repair(self, vin: str, defect_types: list[str]) -> None:
        """Rework fixed the car: the electrical bay replaces a dead lamp, so it lights again.

        The only fault this backend can put on a car is a dark lamp (`faults`), so that is the only
        repair it has to model; the fake world's `repair` is the same rule. Without it a repaired
        car kept its dead bulb and re-inspection could never pass.
        """
        if any(t in defect_types for t in ("brake_light", "lamp", "indicator", "headlight", "electrical")):
            self.faults.pop(vin, None)

    # ---------- flow 2: the driving system asks for help ----------

    def _obstacle_ahead(self, v: _Vehicle, backwards: bool = False):
        """Nearest vehicle, walker or prop in front of the car (or behind it), within range."""
        here = v.actor.get_transform()
        forward = here.get_forward_vector()
        if backwards:
            forward = carla.Vector3D(-forward.x, -forward.y, -forward.z)
        origin = here.location
        best, best_d = None, self.obstacle_range_m
        for actor in self.world.get_actors():
            if actor.id == v.actor.id:
                continue
            if not actor.type_id.startswith(("vehicle.", "walker.", "static.prop")):
                continue
            # A car parked in a slot or bay is beside the lane, not in it. Measured along the car's
            # heading, a car in a bend points across the slots: at the plant world's yard entry the
            # third car of a run took the second, parked in Y2, for a vehicle in its lane, asked for
            # help, and held its brake with every car behind it waiting.
            if actor.type_id.startswith("vehicle.") and not self._in_a_driving_lane(actor):
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

    def _ignore_parked(self, agent) -> None:
        """Let the agent drive past cars parked off the driving lanes.

        BehaviorAgent decides whether a vehicle is in its way by the driving-lane waypoint nearest
        to it, so a car pulled over into a yard slot reads as a car standing in the lane beside the
        slot. On the plant world the third car of a run stopped at the yard entrance behind the
        second, parked in Y2, and every car after it queued there until the run timed out. A vehicle
        whose own position is on no driving lane (a slot, a bay, the lot rows) is not traffic.
        """
        detect = getattr(agent, "_vehicle_obstacle_detected", None)
        if detect is None:
            return

        def detect_traffic(vehicle_list=None, *args, **kwargs):
            if vehicle_list is not None:
                vehicle_list = [veh for veh in vehicle_list if self._in_a_driving_lane(veh)]
            return detect(vehicle_list, *args, **kwargs)

        agent._vehicle_obstacle_detected = detect_traffic

    def _in_a_driving_lane(self, actor) -> bool:
        """Whether an actor stands on a driving lane, by its own position (not the nearest lane)."""
        world_map = getattr(self, "_driving_map", None)
        if world_map is None:                 # fetched once: get_map() is slow
            world_map = self._driving_map = self.world.get_map()
        wp = world_map.get_waypoint(actor.get_location(), project_to_road=False,
                                    lane_type=carla.LaneType.Any)
        return wp is not None and wp.lane_type == carla.LaneType.Driving

    def _is_plant_car(self, actor) -> bool:
        """Whether an actor is one of the plant's cars that is itself on its way somewhere.

        Only a car with a mission is traffic to wait behind. A plant car with none is parked, and if
        it is in the lane it is an obstruction like any other: waiting behind it is waiting for
        ever, which is exactly what happened when a car stood in production control on the next
        car's route to the yard. That has to come back as a request for help, not silence.
        """
        if not actor.type_id.startswith("vehicle."):
            return False
        other = self.vehicles.get(actor.attributes.get("role_name"))
        return other is not None and other.mission is not None

    def assist_clip(self, v: _Vehicle) -> list[bytes]:
        """The seconds before the stop, from the camera on the car, oldest frame first."""
        if v.onboard_frames is None:
            return []
        try:
            return self._clip_frames(v.onboard_frames, ASSIST_FRAMES)
        except Exception as exc:                # a missing clip must not block the request
            log.warning("assist clip capture failed for %s: %s: %s",
                        v.vin, type(exc).__name__, exc)
            return []

    async def raise_assist(self, vin: str, case_id: str, reason: str, options: list[str],
                           clip_uri: str | None = None) -> None:
        """Make a car stop and ask for help, as the real driving system would."""
        v = self.vehicles[vin]
        v.assist_case = case_id
        v.assists_raised += 1
        v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
        clip = self.assist_clip(v)
        uri = clip_uri or f"gs://marshal/assist/{case_id}.jpg"
        if clip:
            self.assist_clips[case_id] = clip
            # The recorded URI points at the last frame, the one showing what stopped the car.
            uri = self.store_artifact(uri, clip[-1])
        await emit(self._handlers, AssistRequest(
            case_id=case_id, vin=vin, location=v.location, reason=reason, options=options,
            clip_uri=uri,
        ))

    def assist_media(self, case_id: str) -> list[Media]:
        """The seconds before the stop, as an ordered run of frames.

        The design asks for a ten second clip with the request. One still was a stand-in for it;
        the car's camera has been streaming the whole time, so the frames were already there.
        """
        frames = self.assist_clips.get(case_id) or []
        if isinstance(frames, bytes):             # a single frame, from an older recording
            frames = [frames]
        return [Media(mime="image/jpeg", data=data, label=f"frame {i} of {len(frames)}, "
                      "the seconds before the car stopped",
                      uri=f"gs://marshal/assist/{case_id}_f{i}.jpg")
                for i, data in enumerate(frames, start=1)]

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
        if self._is_plant_car(obstacle):
            # Waiting behind another of the plant's own cars is queuing, not being stuck: with
            # several cars, the next one stops behind the car at the gate or on the track until it
            # moves on. Asking for help there would put a "vehicle in lane" card in front of a
            # supervisor every time two cars were released in a row.
            v.stalled_s = 0.0
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
            state = getattr(carla.VehicleLightState, self._lamp_for(v, step))
        v.actor.set_light_state(carla.VehicleLightState(state))

    def _lamp_for(self, v: _Vehicle, step: RoutineStep) -> str:
        """Which lamp this step energises now; the indicators step alternates side."""
        if step.name != "indicators":
            return STEP_LIGHTS[step.name]
        elapsed = max(0.0, step.duration_s - v.step_left_s)
        return INDICATOR_SIDES[int(elapsed / INDICATOR_PERIOD_S) % len(INDICATOR_SIDES)]

    async def _routine_step(self, v: _Vehicle) -> None:
        step = v.routine_queue[0]
        if step.name in MANOEUVRES:
            telemetry = self._manoeuvre(v, step)
            if telemetry is None:
                return                          # still driving the step
            v.routine_queue.pop(0)
            # The car has moved, so its route to the next point is planned again from here: the
            # old plan started where the step began, and its first waypoints are now behind it.
            if v.remaining:
                self._next_destination(v)
        else:
            v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
            v.step_left_s -= self.fixed_delta_s
            if v.step_left_s > 0:
                return
            v.routine_queue.pop(0)
            # Telemetry is what the car's electronics report: healthy, even for a dark lamp.
            telemetry = dict(step.telemetry)
        await emit(self._handlers, TrackStep(
            vin=v.vin, mission_id=v.mission.mission_id, routine=v.mission.routine,
            step=step.step, name=step.name, telemetry=telemetry,
        ))
        nxt = v.routine_queue[0] if v.routine_queue else None
        self._set_lights(v, nxt)
        if nxt is not None:
            v.step_left_s = nxt.duration_s

    def _manoeuvre(self, v: _Vehicle, step: RoutineStep) -> dict | None:
        """Drive one routine step and measure it. The telemetry when it is done, else None.

        reverse: back up 3 m in reverse gear, holding the wheel straight, waiting while anything is
        within REVERSE_CLEARANCE_M behind. Reports the distance travelled and how far it drifted
        sideways.
        brake_test: accelerate along the track to 20 km/h, or until the run-up is used up, then
        brake fully and measure the distance from the moment of braking to standstill.
        """
        t = v.actor.get_transform()
        here, forward = t.location, t.get_forward_vector()
        vel = v.actor.get_velocity()
        kmh = 3.6 * math.hypot(vel.x, vel.y)
        m = v.manoeuvre
        if m is None:
            m = v.manoeuvre = {"name": step.name, "start": (here.x, here.y),
                               "heading": (forward.x, forward.y), "phase": "go", "t": 0.0}
        m["t"] += self.fixed_delta_s
        sx, sy = m["start"]
        hx, hy = m["heading"]
        along = (here.x - sx) * hx + (here.y - sy) * hy        # signed, along the start heading
        lateral = abs(-(here.x - sx) * hy + (here.y - sy) * hx)
        brake = carla.VehicleControl(throttle=0.0, brake=1.0)
        timed_out = m["t"] > MANOEUVRE_MAX_S

        if step.name == "reverse":
            if m["phase"] == "go":
                behind, gap = self._obstacle_ahead(v, backwards=True)
                if -along >= REVERSE_TARGET_M or timed_out:
                    m["phase"] = "stop"
                elif behind is not None and gap < REVERSE_CLEARANCE_M:
                    v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0, reverse=True))
                    return None
                else:
                    v.actor.apply_control(carla.VehicleControl(
                        throttle=0.35 if kmh < 4.0 else 0.0, reverse=True, steer=0.0))
                    return None
            v.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0, reverse=True))
            if kmh > 0.2 and not timed_out:
                return None
            v.manoeuvre = None
            return {"gear": "R", "distance_m": round(-along, 2), "lateral_m": round(lateral, 2),
                    "reverse_lamp": "ON", "completed": not timed_out}

        # brake_test
        if m["phase"] == "go":
            ahead, gap = self._obstacle_ahead(v)
            if (kmh >= BRAKE_TEST_KMH or along >= BRAKE_TEST_RUN_UP_MAX_M or timed_out
                    or (ahead is not None and gap < 12.0)):
                m.update(phase="brake", brake_at=along, start_kmh=kmh,
                         blocked=ahead is not None and gap < 12.0)
            else:
                error = ((math.atan2(hy, hx) - math.atan2(forward.y, forward.x) + math.pi)
                         % (2 * math.pi) - math.pi)
                v.actor.apply_control(carla.VehicleControl(
                    throttle=0.8, steer=max(-0.2, min(0.2, 1.5 * error))))
                return None
        v.actor.apply_control(brake)
        if kmh > 0.2 and m["t"] <= MANOEUVRE_MAX_S + 10.0:
            return None
        v.manoeuvre = None
        return {"start_speed_kmh": round(m["start_kmh"], 1),
                "stopping_distance_m": round(along - m["brake_at"], 2),
                "run_up_m": round(m["brake_at"], 2), "lateral_m": round(lateral, 2),
                "completed": not m["blocked"] and not timed_out}

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
        #
        # Ticking a few times first matters: frames already in flight show the previous world
        # state, which once made a dark lamp look lit because the frame was of the previous,
        # healthy car. Each tick delivers a frame, so after a few the newest held is current.
        # A camera rendering every camera_tick_s needs that many ticks plus two for a frame that
        # postdates the call; four was enough only while every camera rendered on every tick.
        ticks = 4
        tick_s = getattr(self, "camera_tick_s", None)
        if tick_s:
            ticks = max(ticks, math.ceil(tick_s / self.fixed_delta_s) * 2 + 2)
        for _ in range(ticks):
            self._tick_world()
        return self._latest_frame(frames)
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
        # Frames already in flight still show the previous pose, so let several more arrive and
        # keep the last of them.
        before = frames.latest()
        deadline = time.monotonic() + max(2.0, self.frame_timeout_s * (settle_frames + 4))
        settled = 0
        while time.monotonic() < deadline:
            if self._owns_ticks():
                self._tick_world()
            current = frames.latest()
            if current is not None and current is not before:
                settled += 1
                before = current
                if settled > settle_frames:
                    break
            else:
                time.sleep(0.02)
        image = frames.latest()
        if image is None:
            raise RuntimeError("camera produced no frame after moving to the requested pose")
        return self._encode(image)

    def latest_frame(self, buffer) -> bytes:
        """The newest frame this camera has delivered."""
        return self._latest_frame(buffer)

    def clip_from(self, buffer, count: int = CLIP_FRAMES) -> list[bytes]:
        """The recent past of this camera as an ordered sequence, oldest first."""
        return self._clip_frames(buffer, count)

    def clip_frames(self, transform, width: int | None = None,
                    height: int | None = None, count: int = CLIP_FRAMES) -> list[bytes]:
        """An ordered run of recent frames from the camera at this pose, oldest first."""
        width = EVIDENCE_WIDTH if width is None else width
        height = EVIDENCE_HEIGHT if height is None else height
        key = self._camera_key(transform, width, height)
        entry = self._cameras.get(key) or self._make_camera(key, transform, width, height)
        _cam, frames, _receive = entry
        return self._clip_frames(frames, count)

    def _latest_frame(self, frames: "_FrameBuffer") -> bytes:
        """The newest frame already delivered, waiting only if none has been."""
        deadline = time.monotonic() + self.frame_timeout_s * 6
        image = frames.latest()
        while image is None and time.monotonic() < deadline:
            time.sleep(0.02)
            image = frames.latest()
        if image is None:
            raise RuntimeError("camera produced no frame after bounded warm-up")
        return self._encode(image)

    def _clip_frames(self, frames: "_FrameBuffer", count: int) -> list[bytes]:
        """The recent past of this camera as an ordered sequence, oldest first.

        This is the clip the design asks for, assembled from frames the camera had already
        delivered. Nothing is requested at the moment it is wanted, because at that moment the
        driving loop is blocked in the call doing the asking.
        """
        samples = frames.recent(count)
        if not samples:
            latest = frames.latest()
            return [self._encode(latest)] if latest is not None else []
        return [data for _at, data in samples]     # encoded as they arrived

    def _encode(self, image) -> bytes:
        """One CARLA frame as JPEG bytes."""
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
        bp = self._camera_blueprint(width, height)
        frames = _FrameBuffer(window_s=CLIP_WINDOW_S, max_frames=CLIP_FRAMES,
                              encode=self._encode)
        def receive(image, buffer=frames) -> None:
            buffer.append(image)

        cam = self.world.spawn_actor(bp, transform)
        cam.listen(receive)
        entry = self._cameras[key] = (cam, frames, receive)
        return entry

    def _make_movable(self, key, transform, width: int, height: int):
        """Create the movable camera and subscribe it once, for the life of the run."""
        bp = self._camera_blueprint(width, height)
        frames = _FrameBuffer(window_s=1.0, max_frames=4)   # the gate judges single frames only
        def receive(image, buffer=frames) -> None:
            buffer.append(image)

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
        # The cars' own cameras too: all three on each car. Only the forward one used to be released
        # here, so the front and rear evidence cameras outlived every run.
        for v in self.vehicles.values():
            self.release_car_sensors(v)

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

# Which of the car's own cameras sees each step's lamps. Evidence rides with the car because the
# car moves through the routine and a camera fixed to the map loses it.
STEP_VIEW = {
    "headlights": "front", "indicators": "rear", "brake_lights": "rear", "reverse": "rear",
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

    def _pose(self, view: str):
        return _offset(self.ds, self.point, *GATE_CAMERAS[view])

    def prewarm(self, views: list[str] | None = None, fixed: bool = False) -> None:
        """Create the gate sensors before the driving loop starts.

        A camera created mid-tick segfaults the server, so this runs before start(). Without
        `fixed`, one movable sensor is made, for a process that photographs a parked car with no
        loop running (scene 1's gate phase, day 2). With `fixed`, one sensor per view is made and
        left streaming, so a car can be photographed while the loop runs and other cars keep
        driving - see capture().
        """
        wanted = list(GATE_VIEWS if views is None else views)
        if fixed:
            for view in wanted:
                self.ds.prewarm_camera(self._pose(view), EVIDENCE_WIDTH, EVIDENCE_HEIGHT)
            return
        self.ds.prewarm_camera(self._pose(wanted[0]), EVIDENCE_WIDTH, EVIDENCE_HEIGHT,
                               movable=True)

    def capture(self, vin: str, views: list[str] | None = None) -> list[Media]:
        out = []
        running = getattr(self.ds, "_task", None) is not None
        for view in GATE_VIEWS:
            if views is not None and view not in views:
                continue
            if running:
                # Inside the driving loop. A movable camera cannot be used here: moving it and
                # waiting for a frame from the new pose deadlocks, because the loop that would
                # produce that frame is blocked in this call. The fixed cameras have been
                # streaming all along, so the newest frame each one already delivered is the car
                # at the gate now. They must have been prewarmed: creating one here is the
                # mid-tick spawn that segfaults the server.
                key = self.ds._camera_key(self._pose(view), EVIDENCE_WIDTH, EVIDENCE_HEIGHT)
                if key not in self.ds._cameras:
                    raise RuntimeError("gate cameras were not prewarmed before the driving loop "
                                       "started; call gate_camera.prewarm(fixed=True) first")
                data = self.ds.capture(self._pose(view), EVIDENCE_WIDTH, EVIDENCE_HEIGHT)
            else:
                data = self.ds.capture_moving(self._pose(view), EVIDENCE_WIDTH, EVIDENCE_HEIGHT)
            uri = self.ds.store_artifact(f"gs://marshal/gate/{vin}/{view}.jpg", data)
            out.append(Media(mime="image/jpeg", data=data, label=f"{view} camera", uri=uri))
        return out


class CarlaTrackCamera:
    """One frame, or a run of frames, per checklist step, from the view that sees that lamp."""

    def __init__(self, ds: CarlaDrivingSystem, point: str = "test_track"):
        self.ds, self.point = ds, point

    def prewarm(self) -> None:
        """Nothing to do: the cameras are created with the car and stream from then on."""

    def _buffer(self, vin: str, name: str):
        v = self.ds.vehicles.get(vin)
        if v is None:
            return None
        entry = v.evidence.get(STEP_VIEW.get(name, "rear"))
        return entry[1] if entry else None

    def clip(self, vin: str, step: int, name: str) -> Media:
        buffer = self._buffer(vin, name)
        if buffer is None:
            raise RuntimeError(f"{vin} has no evidence camera for {name}")
        data = self.ds.latest_frame(buffer)
        uri = self.ds.store_artifact(f"gs://marshal/track/{vin}/step{step}_{name}.jpg", data)
        return Media(mime="image/jpeg", data=data, label=f"track camera, step {step} {name}",
                     uri=uri)

    def sequence(self, vin: str, step: int, name: str) -> list[Media]:
        """The step as an ordered run of frames, for what one frame cannot show.

        One frame cannot show a side changing, so the indicators step was left unconfirmed - and
        judged from a still anyway it failed a healthy car at confidence 1.0. The frames come from
        the camera's own recent past, because at the moment the step reports in the driving loop is
        blocked in that very call and cannot produce more.
        """
        buffer = self._buffer(vin, name)
        if buffer is None:
            return []
        frames = self.ds.clip_from(buffer)
        out: list[Media] = []
        for i, data in enumerate(frames, start=1):
            uri = self.ds.store_artifact(
                f"gs://marshal/track/{vin}/step{step}_{name}_f{i}.jpg", data)
            out.append(Media(mime="image/jpeg", data=data, uri=uri,
                             label=f"frame {i} of {len(frames)}, step {step} {name}"))
        return out
