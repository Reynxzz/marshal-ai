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
import os
import time
from datetime import datetime, timedelta, timezone

from film_notes import note
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
WRONG_COLOUR = "200,20,20"          # red, on a car whose build sheet says blue


def finished(car: Car) -> bool:
    """A car is through the plant once it is parked in the yard, shipped, or standing in its bay.

    A car sent to rework is marked REWORK the moment it is dispatched, before it has moved, so that
    alone would end the run with the car still driving; it is finished once it has arrived.
    """
    if car.state is CarState.REWORK:
        return car.mission_id is None and (car.location.startswith("rework.")
                                           or car.location == "production_control")
    return car.state in DONE


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
    ap.add_argument("--wrong-colour", type=int, default=0, metavar="N",
                    help="paint car N (1-based) red while its build sheet says blue - demo scene 2")
    ap.add_argument("--late-truck", default=None, metavar="TRUCK:MIN",
                    help="once every car is parked, delay this truck by MIN minutes: the yard rules "
                         "do not cover it, so Gemini re-plans the slots, a supervisor approves, and "
                         "the cars drive to their new slots (flow 1's exception)")
    ap.add_argument("--hazard", action="append", default=[], metavar="PLACE:WHAT",
                    help="report a hazard at a zone or map point, e.g. 'finishing_corner:forklift "
                         "unloading pallets': cars bound for the yard then ask Gemini which way to take "
                         "(flows/route.py). Repeatable.")
    ap.add_argument("--stage-hazards", action="store_true",
                    help="also put each hazard in the world, beside the road (a truck unloading, a worker)")
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


HAZARD_ROLE = "marshal-hazard"


def record_evidence(llm, folder) -> None:
    """Keep what Gemini was given and what it answered, call by call, for the film's panels.

    MARSHAL_EVIDENCE_DIR: each call writes NNNN-task.json (prompt, system, answer, wall time) and its
    images beside it. Wraps the one client every flow shares, so gate, lamps, routes, parking and the
    yard all land here. Read-only: the call and its answer are unchanged.
    """
    import itertools
    import re
    from pathlib import Path

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    original, counter = llm.generate, itertools.count(1)

    def generate(task, prompt, schema=None, media=None, system=""):
        result = original(task, prompt, schema, media, system)
        try:
            base = f"{next(counter):04d}-{task}"
            images = []
            for k, m in enumerate(media or []):
                if m.data:
                    ext = {"image/png": "png", "image/jpeg": "jpg", "video/mp4": "mp4"}.get(m.mime, "bin")
                    name = f"{base}-{k}-{re.sub(r'[^a-z0-9]+', '-', (m.label or 'media').lower()).strip('-')}.{ext}"
                    (folder / name).write_bytes(m.data)
                    images.append({"file": name, "label": m.label})
            answer = result.model_dump() if hasattr(result, "model_dump") else result
            (folder / f"{base}.json").write_text(json.dumps(
                {"task": task, "t": time.time(), "prompt": prompt, "system": system, "images": images,
                 "answer": answer}, default=str, indent=1))
        except Exception as exc:                  # the film must never stop the plant
            logging.getLogger("marshal").warning("could not keep evidence for %s: %s", task, exc)
        return result

    llm.generate = generate


def stage_hazard(rt, place: str) -> int:
    """Put what a reported hazard looks like beside the road, so the film shows why cars avoid it.

    A lorry unloading on the paving, boxes at its tail, a worker at the kerb and a warning sign before
    it. Beside the lane, not in it: the rework bays are reached by CARLA's own road plan, which may
    take this corner, and the stand-in brakes for anything in its path. CARLA has no forklift, and
    its delivery truck (carlacola) carries a real brand's logo, so it is a plain lorry. Returns how
    many actors went in; an earlier run's are removed first.
    """
    import carla

    world = rt.driving.world
    for actor in world.get_actors():
        if actor.attributes.get("role_name") == HAZARD_ROLE:
            actor.destroy()
    points = sorted(p for p in rt.map.points if p == place or p.startswith(place + "."))
    points = [p for p in points if rt.map.points[p].carla]
    if not points:
        return 0
    at = rt.driving.transform(points[len(points) // 2])
    lib = world.get_blueprint_library()
    yaw = math.radians(at.rotation.yaw)
    fwd, right = (math.cos(yaw), math.sin(yaw)), (-math.sin(yaw), math.cos(yaw))   # CARLA: y to the right

    def where(ahead: float, side: float, z: float = 0.3, turn: float = 0.0):
        loc = carla.Location(at.location.x + fwd[0] * ahead + right[0] * side,
                             at.location.y + fwd[1] * ahead + right[1] * side, at.location.z + z)
        return carla.Transform(loc, carla.Rotation(yaw=at.rotation.yaw + turn))

    staged = 0
    for blueprint, pose in (("vehicle.carlamotors.european_hgv", where(0.0, 5.5, 0.5)),
                            ("static.prop.box01", where(-4.2, 4.6)), ("static.prop.box02", where(-4.8, 5.6)),
                            ("static.prop.box03", where(-4.4, 6.5)), ("static.prop.creasedbox02", where(-5.6, 4.9)),
                            ("static.prop.warningconstruction", where(-12.0, 3.4, turn=180.0)),
                            ("static.prop.trafficcone01", where(-7.0, 3.3)), ("static.prop.trafficcone01", where(3.0, 3.3)),
                            ("walker.pedestrian.0030", where(-3.0, 3.6, 1.0, turn=90.0))):
        found = lib.filter(blueprint)
        if not found:
            continue
        bp = found[0]
        if bp.has_attribute("role_name"):
            bp.set_attribute("role_name", HAZARD_ROLE)
        if bp.has_attribute("is_invincible"):
            bp.set_attribute("is_invincible", "true")
        actor = world.try_spawn_actor(bp, pose)
        if actor is not None:
            staged += 1
            if blueprint.startswith("vehicle."):         # parked: it settles onto its wheels, braked
                actor.apply_control(carla.VehicleControl(hand_brake=True, brake=1.0))
                actor.set_light_state(carla.VehicleLightState(carla.VehicleLightState.Position))
    return staged


def clear_records(rt, vins: list[str], prefix: str | None = None) -> None:
    """Remove this run's VINs from the store, so no wait is ended by a previous run's record.

    And every other car with the run's prefix: the store is shared, and a VIN-P6 left by an earlier
    six-car run was in the yard state Gemini was shown, so its re-plan for the late truck moved a
    car that did not exist here and the plan check rejected the whole plan.
    """
    if prefix:
        vins = sorted(set(vins) | {c.vin for c in rt.store.list(Car) if c.vin.startswith(prefix)})
    for vin in vins:
        for model, key in ((Car, vin), (CarReport, vin), (InspectionResult, f"{vin}:visual"),
                           (InspectionResult, f"{vin}:functional")):
            if rt.store.get(model, key) is not None:
                rt.store.delete(model, key)


class Watch:
    """Reports each car's progress, and measures what "several cars at once" amounts to."""

    def __init__(self, rt, vins: list[str], app=None):
        self.app = app                     # for the yard's route log (narrate)
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
            now = (car.state, car.location, car.yard_slot)
            if self.last.get(car.vin) != now:
                self.narrate(car, self.last.get(car.vin))
                if car.state is CarState.INSPECTION_GATE and car.vin not in self.released_at:
                    self.released_at[car.vin] = self.sim_s()
                    self.release_gaps.append(self.gap_to_released(car.vin))
                if car.state is CarState.YARD and car.vin not in self.yard_at:
                    self.yard_at[car.vin] = self.sim_s()
                emit({"type": "plant.car", "vin": car.vin, "state": car.state.value,
                      "location": car.location, "sim_s": self.sim_s()})
                self.last[car.vin] = now
            if car.vin in self.released_at and not finished(car):
                in_plant += 1
                if self.speed_kmh(car.vin) > 0.5:
                    moving += 1
        self.max_in_plant = max(self.max_in_plant, in_plant)
        self.max_moving = max(self.max_moving, moving)
        return [c for c in cars if c is not None]

    def narrate(self, car: Car, before: tuple | None) -> None:
        """Say what was just decided about this car, for the film (scripts/film_notes.py)."""
        world, vin = self.rt.driving.world, car.vin
        state, slot = car.state, car.yard_slot
        was_state = before[0] if before else None
        was_slot = before[2] if before else None
        if state is CarState.INSPECTION_GATE and was_state is CarState.LINE_END:
            note(world, f"{vin} released to the inspection gate: fewer than 2 cars queued there, "
                        "and the line end is clear 25 m ahead", "rules")
        elif state is CarState.TEST_TRACK and was_state is CarState.INSPECTION_GATE:
            r = self.rt.store.get(InspectionResult, f"{vin}:visual")
            if r is not None:
                found = (f"{len(r.findings)} finding(s)" if r.findings else "no damage") + \
                    (", colour matches the build sheet" if not r.build_sheet_mismatches
                     else f", {'; '.join(r.build_sheet_mismatches)}")
                note(world, f"Gate photos of {vin}: {r.verdict.value}, {found}", "gemini")
        elif state is CarState.REPORT and was_state is CarState.TEST_TRACK:
            r = self.rt.store.get(InspectionResult, f"{vin}:functional")
            if r is not None:
                checks = ", ".join(f"{c.name} {'PASS' if c.passed else 'FAIL'}" for c in r.checklist)
                note(world, f"Test track, {vin}: {checks or r.verdict.value}", "gemini")
        if slot and slot != was_slot:
            truck = car.build_sheet.truck_id
            nxt = self.rt.plant.next_truck()
            if was_slot:
                note(world, f"{vin} moves {was_slot} to {slot}: truck {truck} now loads "
                            f"{'first' if nxt and nxt.truck_id == truck else 'later'}", "plant")
            else:
                first = nxt is not None and nxt.truck_id == truck
                note(world, f"{vin} passed; slot {slot}. Its truck {truck} loads "
                            f"{'next, so a slot nearest the loading bay' if first else 'later, so a slot farther from the bay'}",
                     "rules")
        if slot and slot != was_slot and car.route_by == "gemini" and self.app is not None:
            record = next((r for r in reversed(self.app.yard.route_log) if r["vin"] == vin), None)
            if record is not None:
                why = "; ".join(h for w in record["ways"] for h in w["hazards"] + w["stopped"]) or "traffic"
                note(world, f"{vin} to {slot} {record['choice']} ({record['probs'][record['choice']]:.2f}): {why}",
                     "gemini")
        if state is CarState.REWORK and was_state is not CarState.REWORK:
            note(world, f"{vin} booked into rework", "gemini")

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
      - Yard re-plans are approved as proposed. They come up when a bay is full: in one run a car
        wanting the body bay found it taken, Gemini proposed sending it on, and with nobody to
        approve that the car sat on the gate and every car behind it waited.
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
        note(rt.driving.world, f"{SUPERVISOR} cleared {vin}'s gate review", "supervisor")
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
    for proposal in rt.pending("replan"):
        decision = await app.yard.decide(proposal.proposal_id, approve=True, by=SUPERVISOR)
        note(rt.driving.world, f"{SUPERVISOR} approved Gemini's re-plan; "
                               f"{len(decision.get('moved', []))} car(s) move", "supervisor")
        emit({"type": "plant.supervisor", "decision": "re-plan approved",
              "summary": proposal.summary, "result": decision})


async def run(args) -> dict:
    rt = build_runtime(Settings(driving="carla", llm="gemini"), host=args.host, port=args.port)
    app = MarshalApp(rt)
    # MARSHAL_PARKING=gemini: Gemini parks each car in its slot; its choices go in the film's feed
    rt.driving.park_note = lambda text: note(rt.driving.world, text, "gemini")
    if type(rt.llm).__name__ != "GeminiLLM":
        raise RuntimeError(f"expected GeminiLLM, got {type(rt.llm).__name__}")
    if os.environ.get("MARSHAL_EVIDENCE_DIR"):
        record_evidence(rt.llm, os.environ["MARSHAL_EVIDENCE_DIR"])

    vins = [f"{args.prefix}{i + 1}" for i in range(args.cars)]
    clear_records(rt, vins, args.prefix)

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

    for spec in args.hazard:
        place, what = spec.split(":", 1)
        rt.plant.hazards[place.strip()] = what.strip()
        note(rt.driving.world, f"Reported at {place.strip()}: {what.strip()}", "plant")
        if args.stage_hazards:
            emit({"type": "plant.hazard_staged", "place": place.strip(), "actors": stage_hazard(rt, place.strip())})

    # The routine each car runs, when the router does not say otherwise.
    steps = parse_steps(args.steps)
    start_routine = app.functional.start

    async def start_with_steps(vin, chosen=None):
        return await start_routine(vin, chosen if chosen is not None else steps)
    app.functional.start = start_with_steps

    sun = set_sun(rt, args.sun_altitude)
    # Every camera is created before the loop starts: a camera spawned mid-tick segfaults the
    # server. That is the gate's four and each car's own three.
    wrong = vins[args.wrong_colour - 1] if args.wrong_colour else None
    # Every car of an earlier run with this prefix goes, not only this run's VINs: a four-car run
    # stopped early left VIN-P4 in the lane, and the next three-car run's first car stood behind it
    # on the test track asking for help that nobody in this scene answers.
    leftovers = {a.attributes.get("role_name") for a in rt.driving.world.get_actors().filter("vehicle.*")
                 if (a.attributes.get("role_name") or "").startswith(args.prefix)}
    for vin in sorted(leftovers | set(vins)):
        rt.driving.despawn(vin)
    for i, vin in enumerate(vins):
        rt.driving.spawn(vin, at="line_end", colour=WRONG_COLOUR if vin == wrong else CAR_COLOUR,
                         behind_m=i * args.queue_gap_m)
    rt.gate_camera.prewarm(fixed=True)
    emit({"type": "plant.start", "cars": vins, "trucks": {t: rt.plant.trucks[t].eta for t in trucks},
          "next_truck": rt.plant.next_truck().truck_id, "steps": steps or "all", "sun": sun,
          "wrong_colour": wrong,
          "cameras": len(rt.driving._cameras) + 3 * len(vins)})

    watch = Watch(rt, vins, app)
    late = None
    if args.late_truck:
        truck_id, minutes = args.late_truck.split(":")
        late = (truck_id, float(minutes))
    replanned = False
    said = 0                                 # yard decisions already noted for the film
    await rt.driving.start()
    note(rt.driving.world, f"{len(vins)} finished cars at the line end, for trucks "
                           f"{', '.join(trucks)}; {rt.plant.next_truck().truck_id} loads first", "plant")
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
            for d in app.yard.decisions[said:]:
                if d.get("status") == "rejected_plan":
                    note(rt.driving.world, "Gemini's plan rejected by the plan check: "
                                           + "; ".join(d.get("errors", [])), "rules")
                elif d.get("summary"):
                    note(rt.driving.world, f"{d['summary']} {d.get('reasoning', '')}".strip(), "gemini")
                said += 1
            if not args.no_supervisor:
                await supervise(app, rt)
            cars = watch.poll()
            if cars and all(finished(c) for c in cars):
                if late and not replanned:
                    # Flow 1's exception: the next truck is late. The rules have no answer for
                    # it; Gemini re-plans the slots on the next tick, the supervisor approves.
                    truck = rt.plant.trucks[late[0]]
                    truck.eta = truck.eta + timedelta(minutes=late[1])
                    replanned = True
                    note(rt.driving.world, f"Truck {late[0]} is {late[1]:.0f} min late; "
                                           f"{rt.plant.next_truck().truck_id} now loads first. "
                                           "No rule covers this: it goes to Gemini", "plant")
                    emit({"type": "plant.late_truck", "truck": late[0], "minutes": late[1],
                          "next": rt.plant.next_truck().truck_id})
                    await asyncio.sleep(1.0)
                    continue
                if any(c.mission_id for c in cars) or any(app.yard.deferred.values()):
                    # Re-slotted cars still driving. This has to yield as the loop's end does: a bare
                    # `continue` spun here without ever awaiting, the driving loop never got a turn,
                    # and the simulation stood still at the moment the re-slot missions were sent.
                    await asyncio.sleep(1.0)
                    continue
                break
            await asyncio.sleep(1.0)
        else:
            emit({"type": "plant.timeout", "after_s": args.timeout})
    finally:
        await rt.driving.stop()

    shipped = {}
    for truck in rt.plant.trucks_by_eta():
        shipped[truck.truck_id] = app.yard.ship(truck.truck_id)
        note(rt.driving.world, f"Truck {truck.truck_id} loaded: {', '.join(shipped[truck.truck_id]) or 'nothing'}",
             "plant")
    cars = watch.poll()
    # Where each car actually stands against the spot its record names, and how squarely: a record
    # saying yard.Y4 is only true if the car is on Y4, facing along it.
    parked = {}
    for c in cars:
        v = rt.driving.vehicles.get(c.vin)
        if v is None or c.location not in rt.driving.map.points or not rt.driving.map.points[c.location].carla:
            continue
        here, spot = v.actor.get_transform(), rt.driving.transform(c.location)
        turn = (here.rotation.yaw - spot.rotation.yaw + 180.0) % 360.0 - 180.0
        parked[c.vin] = {"at": c.location, "off_m": round(here.location.distance(spot.location), 2),
                         "angle_deg": round(turn, 1)}
    for vin in vins:
        rt.driving.despawn(vin)

    by_vin = {c.vin: c for c in cars}
    visual = {vin: rt.store.get(InspectionResult, f"{vin}:visual") for vin in vins}
    reports = {vin: (r.verdict.value if (r := rt.store.get(CarReport, vin)) else None)
               for vin in vins}
    bays = {vin: [t.bay for t in r.rework] if (r := rt.store.get(CarReport, vin)) else []
            for vin in vins}
    line_to_yard = {vin: round(watch.yard_at[vin] - watch.released_at[vin], 1)
                    for vin in vins if vin in watch.yard_at and vin in watch.released_at}
    return {
        "cars": {vin: {"state": by_vin[vin].state.value, "location": by_vin[vin].location,
                       "truck": by_vin[vin].build_sheet.truck_id, "report": reports[vin],
                       "gate": visual[vin].verdict.value if visual[vin] else None,
                       "mismatches": visual[vin].build_sheet_mismatches if visual[vin] else [],
                       "booked_bays": bays[vin]}
                 for vin in vins if vin in by_vin},
        "wrong_colour": wrong,
        "parked": parked,
        "shipped": shipped,
        "max_cars_in_plant_at_once": watch.max_in_plant,
        "max_cars_moving_at_once": watch.max_moving,
        "gap_to_nearest_released_car_m": watch.release_gaps,
        "line_to_yard_sim_s": line_to_yard,
        "gate_false_alarms": FALSE_ALARMS,
        # The speed each car was photographed at by the gate: the design's drive-through, 5 km/h.
        "gate_pass_kmh": dict(getattr(rt.driving, "passes", {})),
        # Each Gemini parking session: every decision, the paths it was offered and any safety stop,
        # so a car handed to a person can be explained afterwards (MARSHAL_PARKING=gemini).
        "parking": dict(getattr(rt.driving, "parking_log", {})),
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
    wrong = out["wrong_colour"]
    good = {vin: c for vin, c in out["cars"].items() if vin != wrong}
    every_good_car_shipped = all(c["state"] == "shipped" for c in good.values())
    concurrent = out["max_cars_in_plant_at_once"] >= 2
    caught = True
    if wrong:
        # Scene 2's claim, as the design states it: the car is flagged at the gate for its colour,
        # the report fails it, it is booked into production control - the design's bay for a wrong
        # variant - and it does not ship. It need not be standing in production control yet: if
        # anything else was found, an earlier bay comes first, and moving between bays waits for
        # the bay to sign the repair off, which this scene does not simulate.
        c = out["cars"].get(wrong, {})
        caught = (c.get("gate") == "FAIL"
                  and any(m.startswith("colour") for m in c.get("mismatches", []))
                  and c.get("report") == "FAIL"
                  and "production_control" in c.get("booked_bays", [])
                  and c.get("state") != "shipped")
        out["wrong_colour_caught"] = caught
    ok = every_good_car_shipped and concurrent and caught and len(out["cars"]) == args.cars
    emit({"type": "plant.done", **out,
          "result": "success" if ok else "not demonstrated"})
    return 0 if ok else 2


if __name__ == "__main__":
    # Skips interpreter shutdown, where the CARLA client intermittently aborts; see
    # exit_without_teardown.
    from marshal_ai.carla_connection import exit_without_teardown

    exit_without_teardown(main())
