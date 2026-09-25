"""The driving system boundary.

`DrivingSystem` is what a backend implements (fake for local dev, CARLA BehaviorAgent
for the hackathon, a real plant AV stack later). `MissionClient` is what agents use:
it checks every command against the design's rules before it leaves Marshal and
forwards driving system events onto the bus.
"""

from __future__ import annotations

import inspect
import logging
from typing import Awaitable, Callable, Protocol

from marshal_ai.driving.messages import (
    AssistChoose, AssistRequest, Command, Event, MissionGo, Status, TrackStep,
)
from marshal_ai.driving.routines import ROUTINES
from marshal_ai.events import EventBus, Topic
from marshal_ai.plant import PlantMap

log = logging.getLogger(__name__)

EventHandler = Callable[[Event], "Awaitable[None] | None"]


class DrivingSystem(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send(self, command: Command) -> None: ...
    def on_event(self, handler: EventHandler) -> None: ...


class CommandRejected(ValueError):
    """A command broke a mission API rule and was not sent."""


class MissionClient:
    def __init__(self, backend: DrivingSystem, plant_map: PlantMap, bus: EventBus | None = None):
        self.backend = backend
        self.map = plant_map
        self.bus = bus
        self.open_requests: dict[str, AssistRequest] = {}
        self.last_status: dict[str, Status] = {}
        self._listeners: list[EventHandler] = []
        backend.on_event(self._handle_event)

    def on_event(self, handler: EventHandler) -> None:
        self._listeners.append(handler)

    async def send(self, command: Command) -> None:
        self._check(command)
        await self.backend.send(command)
        if isinstance(command, AssistChoose):
            self.open_requests.pop(command.case_id, None)

    def _check(self, command: Command) -> None:
        if isinstance(command, MissionGo):
            if not command.waypoints:
                raise CommandRejected("mission.go needs at least one waypoint")
            for wp in command.waypoints:
                if not self.map.has_point(wp):
                    raise CommandRejected(f"unknown map point: {wp}")
            if command.routine is not None:
                if command.routine not in ROUTINES:
                    raise CommandRejected(f"unknown routine {command.routine}")
                if "test_track" not in command.waypoints:
                    raise CommandRejected("a test routine mission must pass test_track")
            for a, b in zip(command.waypoints, command.waypoints[1:]):
                try:
                    self.map.route(a, b)
                except ValueError as e:
                    raise CommandRejected(str(e)) from e
        elif isinstance(command, AssistChoose):
            request = self.open_requests.get(command.case_id)
            if request is None:
                raise CommandRejected(f"no open assist request {command.case_id}")
            if command.option not in request.options:
                raise CommandRejected(
                    f"option {command.option!r} was not offered; choose from {request.options}"
                )
            if not command.chosen_by:
                raise CommandRejected("assist.choose must name the human who decided")

    async def _handle_event(self, event: Event) -> None:
        if isinstance(event, AssistRequest):
            self.open_requests[event.case_id] = event
        elif isinstance(event, Status):
            self.last_status[event.vin] = event
        if self.bus is not None:
            if isinstance(event, AssistRequest):
                topic = Topic.ASSIST_REQUESTS
            elif isinstance(event, TrackStep):
                topic = Topic.TRACK_STEPS
            else:
                topic = Topic.VEHICLE_STATUS
            await self.bus.publish(topic, event)
        for handler in self._listeners:
            result = handler(event)
            if inspect.isawaitable(result):
                await result


async def emit(handlers: list[EventHandler], event: Event) -> None:
    """Deliver an event to backend subscribers; one failing handler does not stop the others."""
    for handler in handlers:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("driving event handler failed for %s", event.type)
