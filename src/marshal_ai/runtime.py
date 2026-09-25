"""Process-wide wiring: plant map, bus, store, mission client, cameras and LLM, built once from settings."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from marshal_ai.cameras import GateCamera, TrackCamera
from marshal_ai.config import Settings, settings as default_settings
from marshal_ai.driving import DrivingSystem, MissionClient, make_driving_system
from marshal_ai.events import EventBus, make_bus
from marshal_ai.fakeworld import FakeGateCamera, FakeTrackCamera, FakeWorld
from marshal_ai.llm import LLM, GeminiLLM
from marshal_ai.plant import PlantMap, load_plant_map
from marshal_ai.plant.state import PlantState
from marshal_ai.records import now
from marshal_ai.store import Store, make_store


@dataclass
class SimClock:
    """Simulated time for the fake plant, so timeouts and KPIs follow simulated seconds."""

    t: datetime = field(default_factory=now)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


@dataclass
class Proposal:
    """Something an agent proposes and a human must decide: a re-plan, a rework, a visual review."""

    proposal_id: str
    kind: str                     # "replan" | "rework" | "review"
    summary: str
    payload: dict
    status: str = "pending"       # pending | approved | rejected
    decided_by: str | None = None


@dataclass
class Runtime:
    settings: Settings
    map: PlantMap
    bus: EventBus
    store: Store
    driving: DrivingSystem
    missions: MissionClient
    llm: LLM
    gate_camera: GateCamera
    track_camera: TrackCamera
    plant: PlantState = field(default_factory=PlantState)
    world: FakeWorld | None = None            # simulation ground truth (fake backend only)
    clock: Callable[[], datetime] = now
    proposals: dict[str, Proposal] = field(default_factory=dict)
    _ids: dict[str, itertools.count] = field(default_factory=dict)
    _started: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def ensure_started(self) -> None:
        async with self._lock:
            if not self._started:
                await self.driving.start()
                self._started = True

    def next_id(self, prefix: str, width: int = 3) -> str:
        counter = self._ids.setdefault(prefix, itertools.count(1))
        return f"{prefix}-{next(counter):0{width}d}"

    def propose(self, kind: str, summary: str, payload: dict) -> Proposal:
        p = Proposal(self.next_id("P"), kind, summary, payload)
        self.proposals[p.proposal_id] = p
        return p

    def pending(self, kind: str | None = None) -> list[Proposal]:
        return [p for p in self.proposals.values()
                if p.status == "pending" and (kind is None or p.kind == kind)]


_runtime: Runtime | None = None


def build_runtime(s: Settings = default_settings, llm: LLM | None = None,
                  world: FakeWorld | None = None, **driving_kwargs) -> Runtime:
    plant_map = load_plant_map()
    bus = make_bus(s.bus, s.gcp_project)
    store = make_store(s.store, s.gcp_project)
    if s.driving == "carla":
        driving_kwargs.setdefault("host", s.carla_host)
        driving_kwargs.setdefault("port", s.carla_port)
    driving = make_driving_system(s.driving, plant_map, **driving_kwargs)
    if llm is None:
        if s.llm == "offline":
            from marshal_ai.offline import offline_llm
            llm = offline_llm()
        else:
            llm = GeminiLLM(s.model)
    if s.driving == "carla":
        from marshal_ai.driving.carla_backend import CarlaGateCamera, CarlaTrackCamera
        gate, track = CarlaGateCamera(driving), CarlaTrackCamera(driving)
    else:
        world = world or FakeWorld()
        gate, track = FakeGateCamera(world), FakeTrackCamera(world)
    return Runtime(s, plant_map, bus, store, driving, MissionClient(driving, plant_map, bus),
                   llm, gate, track, world=world,
                   clock=SimClock() if s.driving == "fake" else now)


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


def set_runtime(rt: Runtime | None) -> None:
    """Swap the runtime, e.g. in tests or the demo seeder."""
    global _runtime
    _runtime = rt
