"""The API's in-process demo plant, and the control channel behind the dashboard's buttons.

In live mode the control tower API is read-only: the plant runs on the CARLA host and the API only
relays its records and events. On a laptop there is no plant to relay, so with
``MARSHAL_RUN_PLANT=1`` the API runs the same marshal_ai app on the fake driving system, on its own
event bus, and ``/api/v1/events`` carries the plant's decisions to the dashboard. This module owns
that plant and what the dashboard can do to it: spawn a car, raise an exception (a stuck car asking
for help), decide its case, restart it.

Gemini judges the route decisions (the text-only calls); the media judgements stay offline, because
their images only resolve in Cloud Storage on the host (a Vertex call with a gs:// URL there gets a
403 without bucket access). Parking on this path is the stand-in: the fake driving system has no
parking hook.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
from datetime import timedelta

from marshal_ai.app import MarshalApp
from marshal_ai.config import settings as ai_settings
from marshal_ai.demo import BOX_SCENE, DEMO_CARS, seed_demo
from marshal_ai.driving import MissionGo
from marshal_ai.llm import LLM
from marshal_ai.plant.state import Truck
from marshal_ai.records import AssistanceCase, Car, CarState
from marshal_ai.runtime import build_runtime

from marshal_api.bridge import get_event_bus

log = logging.getLogger("marshal.plant")

IN_FLIGHT = 4              # spawned cars allowed before the yard is asked for another one
TRUCK_CYCLE_MIN = 8        # a demo truck loads and leaves, freeing slots, then comes again
ASSIST_OPTIONS = ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"]


class PlantNotRunning(RuntimeError):
    """This API is not running the demo plant (MARSHAL_RUN_PLANT=1)."""


def _llm() -> LLM | None:
    """None follows MARSHAL_LLM; with Gemini, only the route decisions leave the machine."""
    if ai_settings.llm != "gemini":
        return None
    from marshal_ai.llm import GeminiLLM, RoutedLLM
    from marshal_ai.offline import offline_llm

    return RoutedLLM(offline_llm(), {"route.": GeminiLLM(ai_settings.model)})


def _spawn(rt, n: int) -> str:
    """A fresh clean car at the line end, from the demo's template."""
    template, truth = DEMO_CARS[0]
    vin = f"VIN-P{n}"
    rt.store.put(template.model_copy(update={"vin": vin, "updated_at": rt.clock(),
                                             "location": "line_end"}))
    if rt.world is not None:
        rt.world.add(vin, copy.deepcopy(truth))
    rt.driving.spawn(vin, "line_end")
    return vin


def _in_flight(rt) -> int:
    return sum(c.state not in (CarState.YARD, CarState.SHIPPED) for c in rt.store.list(Car))


def _load_due_trucks(app: MarshalApp) -> None:
    """A truck whose time has come leaves with its parked cars, then is due again later.

    Without this the yard fills up and the plant stops dispatching; the CARLA host has real
    trucks, the laptop demo keeps its own.
    """
    rt = app.rt
    now = rt.clock()
    for truck_id, truck in list(rt.plant.trucks.items()):
        if truck.eta > now:
            continue
        loaded = app.yard.ship(truck_id)
        rt.plant.trucks[truck_id] = Truck(truck_id, now, now + timedelta(minutes=TRUCK_CYCLE_MIN))
        log.info("plant: truck %s left with %s", truck_id, loaded or "no cars")


def case_dto(case: AssistanceCase) -> dict:
    """The dashboard's view of one assistance case, options in the desk's ranking order."""
    order = {r.get("option"): i for i, r in enumerate(case.ranking)}
    ranked = sorted(case.options, key=lambda o: order.get(o, len(order)))
    by_option = {r.get("option"): r for r in case.ranking}
    return {
        "case_id": case.case_id, "vin": case.vin, "location": case.location,
        "situation": case.situation, "status": "RESOLVED" if case.resolved_by else "OPEN",
        "recommended": case.recommended,
        "confidence": None if case.confidence is None else round(case.confidence, 2),
        "choice": case.choice, "resolved_by": case.resolved_by,
        "options": [
            {"id": option, "rank": i + 1,
             "risk": by_option.get(option, {}).get("risk"),
             "reason": by_option.get(option, {}).get("reason")}
            for i, option in enumerate(ranked)
        ],
    }


class Plant:
    """The in-process demo plant: start it, poke it from the dashboard, restart it."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._app: MarshalApp | None = None
        self._speed = 1.0
        self._n = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _require(self) -> MarshalApp:
        if not self.running or self._app is None:
            raise PlantNotRunning("this API is not running the demo plant")
        return self._app

    async def start(self, speed: float = 1.0) -> None:
        if self.running:
            return
        self._speed = speed
        s = dataclasses.replace(ai_settings, driving="fake", store="memory",
                                artifacts="none", history="none")
        rt = build_runtime(s, llm=_llm(), bus=get_event_bus())
        self._app = MarshalApp(rt)
        seed_demo(rt, ["VIN-214"])           # one clean car; the rest arrive as the line frees
        now = rt.clock()
        for truck in rt.plant.trucks.values():   # first departure soon, then every cycle
            truck.eta = min(truck.eta, now + timedelta(minutes=2))
        self._n = 0
        log.info("plant running in-process on the API bus: llm=%s, speed=%sx", ai_settings.llm, speed)
        self._task = asyncio.create_task(self._loop(speed))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._app is not None:
            self._app.close()
            self._app = None

    async def restart(self) -> None:
        await self.stop()
        await self.start(self._speed)

    async def _loop(self, speed: float) -> None:
        app = self._require()
        rt = app.rt
        while True:
            await app.run_for(0.5 * speed)
            _load_due_trucks(app)
            if _in_flight(rt) < IN_FLIGHT:
                log.info("plant: %s joins the line", self.spawn_now())
            await asyncio.sleep(0.5)

    def spawn_now(self) -> str:
        """Put one more clean car on the line, right now."""
        self._n += 1
        return _spawn(self._require().rt, self._n)

    def state(self) -> dict:
        """What the dashboard needs to draw the plant: cars, the open case, the yard's numbers."""
        if not self.running or self._app is None:
            return {"running": False, "speed": self._speed, "assist": None, "cars": [], "kpis": {}}
        rt = self._app.rt
        open_cases = sorted((c for c in rt.store.list(AssistanceCase) if c.resolved_by is None),
                            key=lambda c: c.opened_at)
        return {
            "running": True, "speed": self._speed,
            "assist": case_dto(open_cases[0]) if open_cases else None,
            "cars": [{"vin": c.vin, "state": c.state.value, "location": c.location}
                     for c in rt.store.list(Car)],
            "kpis": self._app.yard.kpis(),
        }

    async def exception(self) -> dict:
        """A car stops on a cardboard box and asks for help, as the driving system would.

        The assistance desk ranks the options on the bus (Gemini, or the offline stand-in), and
        the case is returned once it exists.
        """
        app = self._require()
        rt = app.rt
        vin = self.spawn_now()
        await rt.missions.send(MissionGo(mission_id=rt.next_id("M", 4), vin=vin,
                                         waypoints=["yard.Y2"]))
        case_id = rt.next_id("AC")
        if rt.world is not None:
            rt.world.scenes[case_id] = BOX_SCENE
        await rt.driving.raise_assist(vin, case_id, "unknown object in lane", ASSIST_OPTIONS)
        for _ in range(60):                  # the desk ranks it on the bus; wait for the case
            case = rt.store.get(AssistanceCase, case_id)
            if case is not None:
                return case_dto(case)
            await asyncio.sleep(0.05)
        raise RuntimeError("the assistance desk did not open the case")

    async def decide(self, case_id: str, option: str, by: str) -> dict:
        """The human's choice for a stuck car; the car drives on."""
        case = await self._require().assistance.decide(case_id, option, by)
        return case_dto(case)


_plant = Plant()


def get_plant() -> Plant:
    return _plant
