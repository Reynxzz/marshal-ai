"""Process-wide wiring: plant map, bus, store, mission client, cameras and LLM, built once from settings."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from marshal_ai.artifacts import Artifacts, make_artifacts
from marshal_ai.cameras import GateCamera, TrackCamera
from marshal_ai.config import Settings, settings as default_settings
from marshal_ai.driving import DrivingSystem, MissionClient, make_driving_system
from marshal_ai.events import EventBus, make_bus
from marshal_ai.history import History, NoHistory, WithHistory, make_history
from marshal_ai.fakeworld import FakeGateCamera, FakeTrackCamera, FakeWorld
from marshal_ai.llm import LLM, GeminiLLM
from marshal_ai.plant import PlantMap, load_plant_map
from marshal_ai.plant.state import PlantState
from marshal_ai.records import Proposal, ProposalStatus, now
from marshal_ai.store import Store, make_store


SIMULATED = ("fake",)    # backends whose time Marshal advances with tick()


@dataclass
class SimClock:
    """Simulated time for the fake plant, so timeouts and KPIs follow simulated seconds."""

    t: datetime = field(default_factory=now)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


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
    artifacts: Artifacts
    history: History
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
        """Record a proposal for a human, in this process and in the store (the dashboard's copy)."""
        if "P" not in self._ids:
            # Each phase of a demo is its own process, so a counter starting at 1 would reuse
            # P-001 and overwrite an earlier proposal in Firestore. Carry on from the store.
            self._ids["P"] = itertools.count(_last_number(self.store.list(Proposal)) + 1)
        p = Proposal(proposal_id=self.next_id("P"), kind=kind, summary=summary, payload=payload,
                     created_at=self.clock())
        self.proposals[p.proposal_id] = p
        self.store.put(p)
        return p

    def decide(self, proposal_id: str, kind: str, approve: bool, by: str, what: str) -> Proposal:
        """A human's decision on a pending proposal of this kind, recorded and stored.

        `what` names the proposal in the error, e.g. "re-plan", when it is not pending.
        """
        p = self.proposals.get(proposal_id)
        if p is None or p.kind != kind or p.status != ProposalStatus.PENDING:
            raise ValueError(f"{proposal_id} is not a pending {what}")
        p.status = ProposalStatus.APPROVED if approve else ProposalStatus.REJECTED
        p.decided_by, p.decided_at = by, self.clock()
        self.store.put(p)
        return p

    def pending(self, kind: str | None = None) -> list[Proposal]:
        """This process's open proposals. The store also holds earlier runs' proposals, which a
        new run must not act on, so this reads the in-process copy."""
        return [p for p in self.proposals.values()
                if p.status == ProposalStatus.PENDING and (kind is None or p.kind == kind)]


def _last_number(proposals: list[Proposal]) -> int:
    numbers = [int(tail) for tail in (p.proposal_id.rsplit("-", 1)[-1] for p in proposals)
               if tail.isdigit()]
    return max(numbers, default=0)


_runtime: Runtime | None = None


def build_runtime(s: Settings = default_settings, llm: LLM | None = None,
                  world: FakeWorld | None = None, **driving_kwargs) -> Runtime:
    plant_map = load_plant_map()
    bus = make_bus(s.bus, s.gcp_project)
    store = make_store(s.store, s.gcp_project)
    history = make_history(s.history, s.gcp_project)
    if not isinstance(history, NoHistory):
        store = WithHistory(store, history)      # every put also becomes a history row
    if s.driving == "carla":
        driving_kwargs.setdefault("host", s.carla_host)
        driving_kwargs.setdefault("port", s.carla_port)
        driving_kwargs.setdefault("timeout_s", s.carla_timeout_s)
        driving_kwargs.setdefault("expected_version", s.carla_version)
    if s.driving in SIMULATED:
        world = world or FakeWorld()
    driving = make_driving_system(s.driving, plant_map, **driving_kwargs)
    artifacts = make_artifacts(s.artifacts, s.gcp_project, s.bucket)
    # The CARLA backend is what holds real pixels, so it does the uploading.
    if hasattr(driving, "artifacts"):
        driving.artifacts = artifacts
    if llm is None:
        if s.llm == "offline":
            from marshal_ai.offline import offline_llm
            llm = offline_llm()
        else:
            llm = GeminiLLM(s.model, decision_model=s.decision_model, task_models=s.task_models)
    if s.parking == "gemini" and hasattr(driving, "parking_driver"):
        from marshal_ai.driving.parking import GeminiParker
        driving.parking_driver = GeminiParker(llm, s.park_mode)
    if s.driving == "carla":
        from marshal_ai.driving.carla_backend import CarlaGateCamera, CarlaTrackCamera
        gate, track = CarlaGateCamera(driving), CarlaTrackCamera(driving)
    else:
        gate, track = FakeGateCamera(world), FakeTrackCamera(world)
    return Runtime(s, plant_map, bus, store, driving, MissionClient(driving, plant_map, bus),
                   llm, gate, track, artifacts, history, world=world,
                   clock=SimClock() if s.driving in SIMULATED else now)


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


def set_runtime(rt: Runtime | None) -> None:
    """Swap the runtime, e.g. in tests or the demo seeder."""
    global _runtime
    _runtime = rt
