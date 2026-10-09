#!/usr/bin/env python3
"""Several cars through the whole plant at once, on real CARLA and real Gemini.

The design's normal flow (demo scene 1): cars leave the line under the yard rules, pass the
inspection gate and the test-track routine, get a report, park in a slot by truck, and ship. Scene 1
(demo_carla_scene1.py) shows it for one car, one phase per process. This runs it for several cars in
one process, with the app's own event chain driving everything:

    line_end --release rules--> inspection_gate --visual PASS--> test_track (routine)
      --> report_point --report PASS--> yard slot nearest the bay for the next truck --> shipped

It works in one process because nothing waits for a camera inside the driving loop any more: every
camera streams into a buffer, and the gate and the track read the newest frame instead of asking
for one. While Gemini judges a car, the simulation is paused (the loop is synchronous), so cars
never move unobserved; between judgements they all drive at once.

    bash scripts/run_carla_plant.sh                      # 3 cars, 2 trucks
    MARSHAL_PLANT_CARS=4 bash scripts/run_carla_plant.sh

The script plays the supervisor, and prints every decision it makes: gate reviews are cleared, since
these cars have no body damage, and what each one flagged is kept as a false alarm; rework is
confirmed as proposed. Without a supervisor one uncertain photo parks a car on the gate and stops
every car behind it. See supervise().
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone

from marshal_ai.app import MarshalApp
from marshal_ai.config import Settings
from marshal_ai.plant.state import Truck
from marshal_ai.records import BuildSheet, Car, CarReport, CarState, InspectionResult, Verdict
from marshal_ai.runtime import build_runtime

SUPERVISOR = "supervisor-ana"
CAR_COLOUR = "0,80,200"
# The build sheet claims only what the gate can check. The car is blue, and it has a glass roof, but
# no gate camera is high enough to see the roof: they sit at 1.2-1.5 m. Declared, the sunroof was
# judged from photos that do not show it, and came back "build sheet says true, car shows false" at
# confidence 1.0 - the same claim had passed in earlier runs. Undeclared, the variant check ignores
# the roof (it only reports mismatches on fields the sheet states), which is the honest position.
BUILD = dict(model="sedan", colour="blue", options=[])
DONE = {CarState.YARD, CarState.SHIPPED, CarState.REWORK}


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--cars", type=int, default=3)
    ap.add_argument("--prefix", default="VIN-P")
    ap.add_argument("--trucks", default="T-1:20,T-2:60",
                    help="truck_id:minutes until it loads; cars are assigned in turn")
    ap.add_argument("--steps", default="1",
                    help="R-12 steps each car runs on the track, comma separated, or 'all'")
    ap.add_argument("--queue-gap-m", type=float, default=9.0,
                    help="spacing of the cars waiting at the line end")
    ap.add_argument("--sun-altitude", type=float, default=15.0,
                    help="one sun for the whole plant: low enough for lamps, high enough for the gate")
    ap.add_argument("--timeout", type=float, default=1200.0, help="wall-clock seconds")
    ap.add_argument("--no-supervisor", action="store_true",
                    help="leave reviews and rework proposals pending; cars wait where they are")
    return ap


def emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True, default=str), flush=True)


def parse_steps(value: str) -> list[int] | None:
    return None if value.strip().lower() == "all" else [int(x) for x in value.split(",")]


def set_sun(rt, altitude: float, attempts: int = 10) -> float:
    """Set the sun and read it back; straight after a map load the server ignores the first ask."""
    applied = None
    for _ in range(attempts):
        weather = rt.driving.world.get_weather()
        weather.sun_altitude_angle = altitude
        rt.driving.world.set_weather(weather)
        time.sleep(0.5)
        applied = rt.driving.world.get_weather().sun_altitude_angle
        if abs(applied - altitude) <= 0.5:
            return applied
    raise RuntimeError(f"sun altitude did not apply: asked {altitude}, server reports {applied}")


def clear_records(rt, vins: list[str]) -> None:
    """Remove this run's VINs from the store, so no wait is ended by a previous run's record."""
    for vin in vins:
        for model, key in ((Car, vin), (CarReport, vin), (InspectionResult, f"{vin}:visual"),
                           (InspectionResult, f"{vin}:functional")):
            if rt.store.get(model, key) is not None:
                rt.store.delete(model, key)


class Watch:
    """Reports each car's progress, and measures what "several cars at once" amounts to."""

    def __init__(self, rt, vins: list[str]):
        self.rt, self.vins = rt, vins
        self.last: dict[str, tuple] = {}
        self.released_at: dict[str, float] = {}
        self.yard_at: dict[str, float] = {}
        self.max_in_plant = 0
        self.max_moving = 0
        self.release_gaps: list[float] = []

    def sim_s(self) -> float:
        return round(self.rt.driving._simulated_s, 1)

    def speed_kmh(self, vin: str) -> float:
        v = self.rt.driving.vehicles.get(vin)
        if v is None:
            return 0.0
        vel = v.actor.get_velocity()
        return 3.6 * math.sqrt(vel.x ** 2 + vel.y ** 2 + vel.z ** 2)

    def poll(self) -> list[Car]:
        cars = [self.rt.store.get(Car, vin) for vin in self.vins]
        in_plant = moving = 0
        for car in cars:
            if car is None:
                continue
            now = (car.state, car.location)
            if self.last.get(car.vin) != now:
                if car.state is CarState.INSPECTION_GATE and car.vin not in self.released_at:
                    self.released_at[car.vin] = self.sim_s()
                    self.release_gaps.append(self.gap_to_released(car.vin))
                if car.state is CarState.YARD and car.vin not in self.yard_at:
                    self.yard_at[car.vin] = self.sim_s()
                emit({"type": "plant.car", "vin": car.vin, "state": car.state.value,
                      "location": car.location, "sim_s": self.sim_s()})
                self.last[car.vin] = now
            if car.vin in self.released_at and car.state not in DONE:
                in_plant += 1
                if self.speed_kmh(car.vin) > 0.5:
                    moving += 1
        self.max_in_plant = max(self.max_in_plant, in_plant)
        self.max_moving = max(self.max_moving, moving)
        return [c for c in cars if c is not None]

    def gap_to_released(self, vin: str) -> float | None:
        """Metres to the nearest car already released, at the moment this one is released."""
        here = self.rt.driving.position_xy(vin)
        gaps = [math.dist(here, self.rt.driving.position_xy(other))
                for other in self.released_at if other != vin]
        return round(min(gaps), 1) if gaps else None


FALSE_ALARMS: list[dict] = []


async def supervise(app, rt) -> None:
    """Make the decisions the design gives a supervisor, and say each one.

    The design keeps two decisions with a human: whether a flagged car at the gate is really
    defective, and whether a car goes to rework. Left pending, a car waits where it stands - on the
    gate, with every car behind it queued. So the run decides them, and prints each decision:

      - Gate reviews are cleared, because the answer is known: these cars have no body damage. CARLA
        cannot apply any, and nothing here tries. Each finding a cleared review carried is kept as
        a false alarm, since "confirmed defects vs false alarms" is one of the gate's KPIs - clearing
        them quietly would hide exactly the number that says how much the gate can be trusted.
      - A gate result that FAILED is not touched, and neither is a lamp fault: those are real.
      - Rework proposals are confirmed as proposed, and the car drives to the bay they name.
    """
    for proposal in rt.pending("review"):
        vin = proposal.payload["vin"]
        result = rt.store.get(InspectionResult, f"{vin}:visual")
        if result is None:
            continue
        alarms = [{"vin": vin, "zone": f.zone, "type": f.type, "confidence": f.confidence,
                   "observation": f.observation, "image": f.image} for f in result.findings]
        alarms += [{"vin": vin, "mismatch": m} for m in result.build_sheet_mismatches]
        FALSE_ALARMS.extend(alarms)
        await app.visual.decide_review(proposal.proposal_id, confirm_defect=False, by=SUPERVISOR)
        emit({"type": "plant.supervisor", "decision": "review cleared", "vin": vin,
              "why": "the simulated car has no body damage; findings recorded as false alarms"
                     if alarms else "no finding; an all-clear view scored under 0.9",
              "false_alarms": alarms})
    for proposal in rt.pending("rework"):
        vin = proposal.payload["vin"]
        report = rt.store.get(CarReport, vin)
        decision = await app.router.decide(proposal.proposal_id, confirm=True, by=SUPERVISOR)
        emit({"type": "plant.supervisor", "decision": "rework confirmed", "vin": vin,
              "summary": report.summary if report else None, "result": decision})


async def run(args) -> dict:
    rt = build_runtime(Settings(driving="carla", llm="gemini"), host=args.host, port=args.port)
    app = MarshalApp(rt)
    if type(rt.llm).__name__ != "GeminiLLM":
        raise RuntimeError(f"expected GeminiLLM, got {type(rt.llm).__name__}")

    vins = [f"{args.prefix}{i + 1}" for i in range(args.cars)]
    clear_records(rt, vins)

    now = datetime.now(timezone.utc)
    trucks = []
    for spec in args.trucks.split(","):
        truck_id, minutes = spec.split(":")
        at = now + timedelta(minutes=float(minutes))
        rt.plant.trucks[truck_id] = Truck(truck_id=truck_id, scheduled=at, eta=at)
        trucks.append(truck_id)
    for i, vin in enumerate(vins):
        rt.store.put(Car(vin=vin, state=CarState.LINE_END, location="line_end",
                         build_sheet=BuildSheet(**BUILD, truck_id=trucks[i % len(trucks)])))

    # The routine each car runs, when the router does not say otherwise.
    steps = parse_steps(args.steps)
    start_routine = app.functional.start

    async def start_with_steps(vin, chosen=None):
        return await start_routine(vin, chosen if chosen is not None else steps)
    app.functional.start = start_with_steps

    sun = set_sun(rt, args.sun_altitude)
    # Every camera is created before the loop starts: a camera spawned mid-tick segfaults the
    # server. That is the gate's four and each car's own three.
    for i, vin in enumerate(vins):
        rt.driving.despawn(vin)
        rt.driving.spawn(vin, at="line_end", colour=CAR_COLOUR, behind_m=i * args.queue_gap_m)
    rt.gate_camera.prewarm(fixed=True)
    emit({"type": "plant.start", "cars": vins, "trucks": {t: rt.plant.trucks[t].eta for t in trucks},
          "next_truck": rt.plant.next_truck().truck_id, "steps": steps or "all", "sun": sun,
          "cameras": len(rt.driving._cameras) + 3 * len(vins)})

    watch = Watch(rt, vins)
    await rt.driving.start()
    deadline = time.monotonic() + args.timeout
    next_beat = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            if time.monotonic() >= next_beat:
                # Silence looks the same as a hang, so say where every car is twice a minute.
                emit({"type": "plant.progress", "sim_s": watch.sim_s(),
                      "cars": {vin: [c.state.value, c.location, round(watch.speed_kmh(vin), 1)]
                               for vin in vins if (c := rt.store.get(Car, vin))},
                      "proposals": [(p.proposal_id, p.kind, p.status, p.payload.get("vin"))
                                    for p in rt.proposals.values()]})
                next_beat = time.monotonic() + 30
            await app.tick()                    # release rules and yard exceptions
            if not args.no_supervisor:
                await supervise(app, rt)
            cars = watch.poll()
            if cars and all(c.state in DONE for c in cars):
                break
            await asyncio.sleep(1.0)
        else:
            emit({"type": "plant.timeout", "after_s": args.timeout})
    finally:
        await rt.driving.stop()

    shipped = {}
    for truck_id in trucks:
        shipped[truck_id] = app.yard.ship(truck_id)
    cars = watch.poll()
    for vin in vins:
        rt.driving.despawn(vin)

    by_vin = {c.vin: c for c in cars}
    reports = {vin: (r.verdict.value if (r := rt.store.get(CarReport, vin)) else None)
               for vin in vins}
    line_to_yard = {vin: round(watch.yard_at[vin] - watch.released_at[vin], 1)
                    for vin in vins if vin in watch.yard_at and vin in watch.released_at}
    return {
        "cars": {vin: {"state": by_vin[vin].state.value, "slot": by_vin[vin].yard_slot,
                       "truck": by_vin[vin].build_sheet.truck_id, "report": reports[vin]}
                 for vin in vins if vin in by_vin},
        "shipped": shipped,
        "max_cars_in_plant_at_once": watch.max_in_plant,
        "max_cars_moving_at_once": watch.max_moving,
        "gap_to_nearest_released_car_m": watch.release_gaps,
        "line_to_yard_sim_s": line_to_yard,
        "gate_false_alarms": FALSE_ALARMS,
        "simulated_s": watch.sim_s(),
        "realtime_ratio": rt.driving.realtime_ratio(),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parser().parse_args(argv)
    try:
        out = asyncio.run(run(args))
    except Exception as exc:
        emit({"type": "plant.error", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    every_car_shipped = all(c["state"] == "shipped" for c in out["cars"].values())
    concurrent = out["max_cars_in_plant_at_once"] >= 2
    ok = every_car_shipped and concurrent and len(out["cars"]) == args.cars
    emit({"type": "plant.done", **out,
          "result": "success" if ok else "not demonstrated"})
    return 0 if ok else 2


if __name__ == "__main__":
    # Skips interpreter shutdown, where the CARLA client intermittently aborts; see
    # exit_without_teardown.
    from marshal_ai.carla_connection import exit_without_teardown

    exit_without_teardown(main())
