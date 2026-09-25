"""A stand-in driving system for local development and tests.

Cars move along plant map routes at their mission speed. It honours hold, resume
and assist requests the same way the CARLA backend does, so agents can be built
on a laptop without a GPU. `tick()` advances simulated time explicitly; `start()`
runs it in real time (scaled by `time_scale`).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from marshal_ai.driving.api import EventHandler, emit
from marshal_ai.driving.messages import (
    AssistChoose, AssistRequest, Command, MissionDone, MissionGo, MissionHold, MissionResume,
    Status, TrackStep,
)
from marshal_ai.driving.routines import RoutineStep, routine_steps
from marshal_ai.plant import PlantMap


@dataclass
class _Car:
    vin: str
    x: float
    y: float
    location: str
    mission: MissionGo | None = None
    path: list[str] = field(default_factory=list)     # remaining points, next first
    travelled: float = 0.0
    total: float = 0.0
    speed_kmh: float = 0.0
    held: bool = False
    hold_left_s: float | None = None
    assist_case: str | None = None
    routine_queue: list[RoutineStep] = field(default_factory=list)
    routine_done: bool = False
    step_left_s: float = 0.0


class FakeDrivingSystem:
    def __init__(self, plant_map: PlantMap, time_scale: float = 1.0, status_period_s: float = 1.0):
        self.map = plant_map
        self.time_scale = time_scale
        self.status_period_s = status_period_s
        self.cars: dict[str, _Car] = {}
        self._handlers: list[EventHandler] = []
        self._since_status = 0.0
        self._task: asyncio.Task | None = None

    # simulation-only helpers

    def spawn(self, vin: str, at: str = "line_end") -> None:
        p = self.map.points[at]
        self.cars[vin] = _Car(vin=vin, x=p.x, y=p.y, location=at)

    async def raise_assist(self, vin: str, case_id: str, reason: str, options: list[str],
                           clip_uri: str | None = None) -> None:
        """Make a car stop and ask for help, as the real driving system would."""
        car = self.cars[vin]
        car.assist_case = case_id
        car.speed_kmh = 0.0
        await emit(self._handlers, AssistRequest(
            case_id=case_id, vin=vin, location=car.location, reason=reason,
            options=options, clip_uri=clip_uri,
        ))

    # DrivingSystem

    def on_event(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    async def send(self, command: Command) -> None:
        if isinstance(command, MissionGo):
            car = self.cars.get(command.vin)
            if car is None:
                raise KeyError(f"no car {command.vin}")
            stops = [car.location, *command.waypoints]
            path: list[str] = []
            for a, b in zip(stops, stops[1:]):
                path.extend(self.map.route(a, b)[1:])
            car.mission, car.path, car.travelled = command, path, 0.0
            car.routine_queue, car.routine_done = [], False
            car.total = self.map.route_length([car.location, *path]) if path else 0.0
            self._starts_routine(car)                  # already standing on the track
        elif isinstance(command, MissionHold):
            car = self.cars[command.vin]
            car.held, car.hold_left_s = True, command.duration_s
        elif isinstance(command, MissionResume):
            car = self.cars[command.vin]
            car.held, car.hold_left_s = False, None
        elif isinstance(command, AssistChoose):
            car = self.cars[command.vin]
            if car.assist_case == command.case_id:
                car.assist_case = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self, step_s: float = 0.1) -> None:
        while True:
            await asyncio.sleep(step_s)
            await self.tick(step_s * self.time_scale)

    async def tick(self, dt: float) -> None:
        """Advance simulated time by dt seconds."""
        for car in self.cars.values():
            await self._advance(car, dt)
        self._since_status += dt
        if self._since_status >= self.status_period_s:
            self._since_status = 0.0
            for car in self.cars.values():
                await emit(self._handlers, self._status(car))

    async def _advance(self, car: _Car, dt: float) -> None:
        if car.held and car.hold_left_s is not None:
            car.hold_left_s -= dt
            if car.hold_left_s <= 0:
                car.held, car.hold_left_s = False, None
        if car.mission is None or car.held or car.assist_case:
            car.speed_kmh = 0.0
            return
        if car.routine_queue:
            await self._routine(car, dt)
            return
        car.speed_kmh = car.mission.max_speed_kmh
        budget = car.speed_kmh / 3.6 * dt
        while budget > 0 and car.path:
            target = self.map.points[car.path[0]]
            dx, dy = target.x - car.x, target.y - car.y
            gap = (dx * dx + dy * dy) ** 0.5
            if gap <= budget:
                car.x, car.y, car.location = target.x, target.y, target.name
                car.travelled += gap
                budget -= gap
                car.path.pop(0)
                if self._starts_routine(car):
                    return
            else:
                car.x += dx / gap * budget
                car.y += dy / gap * budget
                car.travelled += budget
                budget = 0
        if not car.path:
            mission = car.mission
            car.mission, car.speed_kmh = None, 0.0
            await emit(self._handlers, self._status(car, mission_id=mission.mission_id, progress=1.0))
            await emit(self._handlers, MissionDone(
                mission_id=mission.mission_id, vin=car.vin, location=car.location,
            ))

    def _starts_routine(self, car: _Car) -> bool:
        m = car.mission
        if m.routine is None or car.routine_done or car.location != "test_track":
            return False
        car.routine_queue = routine_steps(m.routine, m.routine_steps)
        car.routine_done = True
        car.speed_kmh = 0.0
        if car.routine_queue:
            car.step_left_s = car.routine_queue[0].duration_s
        return bool(car.routine_queue)

    async def _routine(self, car: _Car, dt: float) -> None:
        car.speed_kmh = 0.0
        car.step_left_s -= dt
        if car.step_left_s > 0:
            return
        step = car.routine_queue.pop(0)
        # Healthy electronics report every step as done, even with a dead lamp.
        await emit(self._handlers, TrackStep(
            vin=car.vin, mission_id=car.mission.mission_id, routine=car.mission.routine,
            step=step.step, name=step.name, telemetry=dict(step.telemetry),
        ))
        if car.routine_queue:
            car.step_left_s = car.routine_queue[0].duration_s

    def _status(self, car: _Car, mission_id: str | None = None, progress: float | None = None) -> Status:
        if progress is None:
            progress = car.travelled / car.total if car.mission and car.total else 0.0
        return Status(
            vin=car.vin, location=car.location, x=car.x, y=car.y, speed_kmh=car.speed_kmh,
            mission_id=mission_id or (car.mission.mission_id if car.mission else None),
            progress=min(progress, 1.0), held=car.held,
            stopped_for_assist=car.assist_case is not None,
        )
