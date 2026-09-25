"""CARLA BehaviorAgent playing the existing, validated driving system.

Runs on the GPU VM next to the CARLA server. Needs the `carla` client package and
CARLA's PythonAPI `agents` package on the path:

    export PYTHONPATH=$CARLA_ROOT/PythonAPI/carla:$PYTHONPATH

Marshal only ever sends missions; this module is where they become BehaviorAgent
destinations. The agent's own collision and hazard stops are never overridden.

Test track routines switch the car's real lights, so the track camera sees them. A car
in `faults` reports a lamp ON in telemetry while leaving it dark, the "dead bulb" case.

Not wired yet: raising assist.request when BehaviorAgent blocks on an obstacle.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from marshal_ai.driving.api import EventHandler, emit
from marshal_ai.driving.messages import (
    AssistChoose, Command, MissionDone, MissionGo, MissionHold, MissionResume, Status, TrackStep,
)
from marshal_ai.driving.routines import RoutineStep, routine_steps
from marshal_ai.fakeworld import GATE_VIEWS
from marshal_ai.llm import Media
from marshal_ai.plant import PlantMap

log = logging.getLogger(__name__)

try:
    import carla
    from agents.navigation.behavior_agent import BehaviorAgent
except ImportError:  # local dev machine without CARLA
    carla = None
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


STEP_LIGHTS = {
    "headlights": "LowBeam", "indicators": "LeftBlinker", "brake_lights": "Brake", "reverse": "Reverse",
}


class CarlaDrivingSystem:
    def __init__(self, plant_map: PlantMap, host: str = "localhost", port: int = 2000,
                 fixed_delta_s: float = 0.05, behavior: str = "cautious",
                 vehicle_blueprint: str = "vehicle.lincoln.mkz_2020"):
        if carla is None:
            raise RuntimeError(
                "CARLA client not available. Install marshal-ai[carla] on the VM and put "
                "$CARLA_ROOT/PythonAPI/carla on PYTHONPATH, or use MARSHAL_DRIVING=fake."
            )
        self.map = plant_map
        self.fixed_delta_s = fixed_delta_s
        self.behavior = behavior
        self.vehicle_blueprint = vehicle_blueprint
        self.client = carla.Client(host, port)
        self.client.set_timeout(10.0)
        self.world = self.client.get_world()
        self.vehicles: dict[str, _Vehicle] = {}
        self._handlers: list[EventHandler] = []
        self._task: asyncio.Task | None = None
        self._since_status = 0.0
        self.faults: dict[str, set[str]] = {}      # vin -> routine steps whose lamp stays dark

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
        agent = BehaviorAgent(actor, behavior=self.behavior)
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
        settings = self.world.get_settings()
        settings.synchronous_mode = False
        self.world.apply_settings(settings)

    async def _run(self) -> None:
        while True:
            for v in list(self.vehicles.values()):
                await self._step(v)
            await asyncio.to_thread(self.world.tick)
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
        v.actor.apply_control(v.agent.run_step())

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

    def capture(self, transform, width: int = 1280, height: int = 720) -> bytes:
        """One RGB frame from a camera placed at a world transform (synchronous mode)."""
        bp = self.world.get_blueprint_library().find("sensor.camera.rgb")
        bp.set_attribute("image_size_x", str(width))
        bp.set_attribute("image_size_y", str(height))
        frames: queue.Queue = queue.Queue()
        cam = self.world.spawn_actor(bp, transform)
        try:
            cam.listen(frames.put)
            self.world.tick()
            image = frames.get(timeout=5.0)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "frame.jpg"
                image.save_to_disk(str(path))
                return path.read_bytes()
        finally:
            cam.stop()
            cam.destroy()

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
            data = self.ds.capture(_offset(self.ds, self.point, *GATE_CAMERAS[view]))
            out.append(Media(mime="image/jpeg", data=data, label=f"{view} camera",
                             uri=f"gs://marshal/gate/{vin}/{view}.jpg"))
        return out


class CarlaTrackCamera:
    """A rear three-quarter camera at the test track; one frame per step (lights are steady)."""

    def __init__(self, ds: CarlaDrivingSystem, point: str = "test_track"):
        self.ds, self.point = ds, point

    def clip(self, vin: str, step: int, name: str) -> Media:
        data = self.ds.capture(_offset(self.ds, self.point, -8.0, 3.0, 1.6, -20.0))
        return Media(mime="image/jpeg", data=data, label=f"track camera, step {step} {name}",
                     uri=f"gs://marshal/track/{vin}/step{step}_{name}.jpg")
