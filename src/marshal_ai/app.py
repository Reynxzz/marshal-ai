"""Marshal: the seven flows wired together through the event topics.

    driving system --vehicle-status / mission.done--> Yard Traffic, gate trigger, Functional Checker, Router
    driving system --assist-requests--> Remote Assistance Desk
    gate trigger   --gate-images--> Visual Inspector
    driving system --track-steps--> Functional Checker
    inspectors     --inspection-results--> Report and Rework Router
    assistance     --test-requests--> Scenario Generator
"""

from __future__ import annotations

import asyncio

from marshal_ai.driving import AssistRequest, MissionDone, TrackStep
from marshal_ai.events import Topic
from marshal_ai.flows.assistance import AssistanceDesk
from marshal_ai.flows.base import log
from marshal_ai.flows.functional import FunctionalChecker
from marshal_ai.flows.report import ReportRouter
from marshal_ai.flows.visual import VisualInspector
from marshal_ai.flows.yard import YardTraffic
from marshal_ai.lab.adversarial import AdversarialTester
from marshal_ai.lab.generator import ScenarioGenerator
from marshal_ai.lab.regression import ReleaseGate
from marshal_ai.lab.runner import CarlaRunner, Runner, SimRunner
from marshal_ai.lab.sim import DriverProfile
from marshal_ai.records import Car, CarState, InspectionResult
from marshal_ai.runtime import Runtime, SimClock

# Driving system releases the lab can evaluate locally. 1.1 adds the fix the demo failure suggests.
RELEASES = {
    "behavior-agent-1.0": DriverProfile(),
    "behavior-agent-1.1": DriverProfile(name="behavior-agent-1.1",
                                        zone_speed_caps_kmh={"finishing_corner@night": 5.0,
                                                             "finishing_corner@dusk": 6.0}),
}


class MarshalApp:
    def __init__(self, rt: Runtime, runner: Runner | None = None):
        self.rt = rt
        self.yard = YardTraffic(self)
        self.assistance = AssistanceDesk(self)
        self.visual = VisualInspector(self)
        self.functional = FunctionalChecker(self)
        self.router = ReportRouter(self)
        if runner is None:
            runner = (CarlaRunner(rt.map, rt.settings.carla_host, rt.settings.carla_port)
                      if rt.settings.driving == "carla" else SimRunner(rt.map, RELEASES["behavior-agent-1.0"]))
        self.runner = runner
        self.generator = ScenarioGenerator(rt, runner)
        self.adversarial = AdversarialTester(rt, runner)
        self.release_gate = ReleaseGate(rt)
        self.test_requests: list[dict] = []
        self.auto_generate = False
        self._loop: asyncio.Task | None = None
        self._subscribe()

    def _subscribe(self) -> None:
        bus = self.rt.bus
        bus.subscribe(Topic.VEHICLE_STATUS, self._on_vehicle_event)
        bus.subscribe(Topic.ASSIST_REQUESTS, self._on_assist)
        bus.subscribe(Topic.GATE_IMAGES, self._on_gate_images)
        bus.subscribe(Topic.TRACK_STEPS, self._on_track_step)
        bus.subscribe(Topic.INSPECTION_RESULTS, self._on_result)
        bus.subscribe(Topic.TEST_REQUESTS, self._on_test_request)

    async def _on_vehicle_event(self, data: dict) -> None:
        if data.get("type") != "mission.done":
            return
        event = MissionDone.model_validate(data)
        car = self.rt.store.get(Car, event.vin)
        if car is not None and event.location == "inspection_gate" and car.state == CarState.INSPECTION_GATE:
            views, check_variant = self.router.views_for(event.vin)
            await self.rt.bus.publish(Topic.GATE_IMAGES, {"vin": event.vin, "views": views,
                                                          "check_variant": check_variant})
        await self.yard.on_mission_done(event)
        await self.router.on_mission_done(event)
        await self.functional.on_mission_done(event)

    async def _on_gate_images(self, data: dict) -> None:
        await self.visual.inspect(data["vin"], data.get("views"), data.get("check_variant", True))

    async def _on_assist(self, data: dict) -> None:
        await self.assistance.on_request(AssistRequest.model_validate(data))

    def _on_track_step(self, data: dict) -> None:
        self.functional.on_step(TrackStep.model_validate(data))

    async def _on_result(self, data: dict) -> None:
        await self.router.on_result(InspectionResult.model_validate(data))

    async def _on_test_request(self, data: dict) -> None:
        self.test_requests.append(data)
        if self.auto_generate:
            await asyncio.to_thread(self.generator.generate, data["text"], data["source"])

    # ---------- running the plant ----------

    async def tick(self) -> None:
        """Once per second: yard rules and exceptions, assistance escalations."""
        await self.yard.tick()
        self.assistance.check_escalations()

    async def run_for(self, seconds: float, dt: float = 0.5) -> None:
        """Advance a fake plant by simulated seconds (fake driving system only)."""
        fake = self.rt.driving
        elapsed, since_tick = 0.0, 1.0
        while elapsed < seconds:
            if since_tick >= 1.0:
                await self.tick()
                since_tick = 0.0
            await fake.tick(dt)
            elapsed += dt
            since_tick += dt
            if isinstance(self.rt.clock, SimClock):
                self.rt.clock.advance(dt)

    async def ensure_running(self, time_scale: float = 1.0) -> None:
        """Run the plant in the background in real time (for chatting with the agents)."""
        if self._loop is not None:
            return
        if self.rt.settings.driving == "fake":
            async def loop():
                while True:
                    await asyncio.sleep(0.5)
                    await self.run_for(0.5 * time_scale)
        else:
            await self.rt.ensure_started()

            async def loop():
                while True:
                    await asyncio.sleep(1.0)
                    await self.tick()
        self._loop = asyncio.create_task(loop())

    def kpis(self) -> dict:
        return {"yard": self.yard.kpis(), "assistance": self.assistance.kpis(),
                "visual": self.visual.kpis(), "functional": self.functional.kpis(),
                "report": self.router.kpis()}


_app: MarshalApp | None = None


def get_app() -> MarshalApp:
    global _app
    if _app is None:
        from marshal_ai.runtime import get_runtime
        _app = MarshalApp(get_runtime())
        log.info("Marshal app started")
    return _app


def set_app(app: MarshalApp | None) -> None:
    global _app
    _app = app
