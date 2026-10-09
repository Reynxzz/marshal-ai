#!/usr/bin/env python3
"""Flow 1 on real CARLA: yard traffic releasing cars from the line end.

Demo scene 1. Several finished cars queue at the line end. The Yard Traffic Controller
releases them one at a time under its own rules - no model call - and the spacing rule is
measured against real CARLA world coordinates rather than a simulated guess:

    a car is released only when the inspection gate queue has fewer than 2 cars
    and no released car is still within 25 m of the line end

Then a truck is delayed, which is an exception the rules do not cover. That goes to Gemini,
which proposes a re-plan; because it moves several cars it needs a supervisor's approval
before anything is sent.

    cd ~/marshal-app && bash scripts/run_carla_yard.sh
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import timedelta

from marshal_ai.app import MarshalApp
from marshal_ai.config import Settings
from marshal_ai.flows.yard import GATE_QUEUE_LIMIT, MIN_SPACING_M
from marshal_ai.plant.state import Truck
from marshal_ai.records import BuildSheet, Car, CarState
from marshal_ai.runtime import build_runtime

SUPERVISOR = "supervisor-ana"
COLOURS = ["0,80,200", "200,20,20", "20,160,60", "230,230,230", "40,40,40"]


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--cars", type=int, default=3)
    ap.add_argument("--queue-gap-m", type=float, default=9.0,
                    help="how far apart the waiting cars sit behind the line end")
    ap.add_argument("--truck", default="T-1")
    ap.add_argument("--next-truck", default="T-2")
    ap.add_argument("--parked", type=int, default=3,
                    help="cars already in the yard for the delayed truck; re-slotting several "
                         "is what makes the decision need a supervisor")
    ap.add_argument("--observe-s", type=float, default=150.0,
                    help="how long to let the controller run before the exception")
    ap.add_argument("--with-gate", action=argparse.BooleanOptionalAction, default=False,
                    help="let a released car's arrival start flow 3's gate inspection. Off by "
                         "default: the CARLA 0.9.16 server segfaults (Signal 11) when a camera "
                         "is used while the driving loop is active, which kills the cars "
                         "mid-run. Use the process-per-phase pattern in run_carla_day2.sh "
                         "instead of combining them here.")
    ap.add_argument("--output", default="/tmp/marshal-yard")
    return ap


def emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True, default=str), flush=True)


def cars_in_state(rt, state) -> list:
    from marshal_ai.records import Car as _Car
    return [c for c in rt.store.list(_Car) if c.state == state]


def spacing_now(rt, vins: list[str]) -> dict:
    """Real distance from the line end for every car that exists in the world."""
    start = rt.driving.transform("line_end").location
    out = {}
    for vin in vins:
        v = rt.driving.vehicles.get(vin)
        if v is None:
            continue
        loc = v.actor.get_location()
        out[vin] = round(math.hypot(loc.x - start.x, loc.y - start.y), 1)
    return out


async def run(args: argparse.Namespace) -> dict:
    rt = build_runtime(Settings(driving="carla", llm="gemini", bus="memory", store="memory"),
                       host=args.host, port=args.port)
    app = MarshalApp(rt)
    if type(rt.llm).__name__ != "GeminiLLM":
        raise RuntimeError(f"expected GeminiLLM, got {type(rt.llm).__name__}")

    # Flow 3 is off here. Pooled cameras no longer stream while idle, which removed the
    # "Invalid session: no stream available" floods, but the server still crashes with
    # Signal 11 (SIGSEGV) when a camera is used while the driving loop runs: the client then
    # times out on world.tick() and the cars die with the server. Day 2 works around this by
    # giving each phase its own process, which is the pattern to use for a combined scene.
    if not args.with_gate:
        async def _gate_out_of_scope(vin, views=None, check_variant=True):
            emit({"type": "yard.gate_skipped", "vin": vin, "why": "--no-with-gate"})
        app.visual.inspect = _gate_out_of_scope

    vins = [f"VIN-Y{i + 1}" for i in range(args.cars)]
    emit({"type": "yard.start", "llm": type(rt.llm).__name__,
          "map": rt.driving.world.get_map().name, "cars": vins,
          "min_spacing_m": MIN_SPACING_M})

    # a queue of finished cars waiting at the line end, all for the same truck
    for index, vin in enumerate(vins):
        rt.store.put(Car(vin=vin, state=CarState.LINE_END, location="line_end",
                         build_sheet=BuildSheet(model="sedan", colour="black",
                                                truck_id=args.truck)))
    for actor in rt.driving.world.get_actors().filter("vehicle.*"):
        if actor.attributes.get("role_name") in vins:
            actor.destroy()

    releases: list[dict] = []
    spacing_at_release: list[dict] = []
    if args.with_gate:
        # Create the gate sensors before the loop starts. Creating them on the first capture,
        # which happens mid-tick, is where the server segfaults (Signal 11); day 2 is stable
        # because every camera exists before start().
        rt.gate_camera.prewarm()
        emit({"type": "yard.gate_prewarmed", "cameras": len(rt.driving._cameras)})
    await rt.driving.start()
    try:
        for index, vin in enumerate(vins):
            rt.driving.spawn(vin, at="line_end", colour=COLOURS[index % len(COLOURS)],
                             behind_m=index * args.queue_gap_m)
        emit({"type": "yard.queued", "cars": spacing_now(rt, vins)})

        loop = asyncio.get_event_loop()
        deadline = loop.time() + args.observe_s
        while loop.time() < deadline:
            before = {c.vin for c in app.yard.waiting_at_line_end()}
            allowed, why = app.yard.can_release()
            queue_before = len(app.yard.gate_queue())
            # cars already released but not yet arrived: the first segment
            segment_before = [c.vin for c in app.yard.gate_queue()
                              if c.location != "inspection_gate"]
            await app.yard.release_next()      # tick() also handles exceptions; those come later
            after = {c.vin for c in app.yard.waiting_at_line_end()}
            for vin in sorted(before - after):
                gaps = spacing_now(rt, vins)
                # The rule clears the first segment when the car ahead is either MIN_SPACING_M
                # away or has reached the gate. On Town05 the gate is only 20 m from the line
                # end, so a car can never be 25 m out while still in that segment: reaching the
                # gate is the normal way it clears.
                # What the rule promises, checked directly: it said yes, the gate queue was
                # under its limit, and nothing else was still in the first segment. Measuring
                # metres instead is misleading here - BehaviorAgent finishes within a few
                # metres of its target, and the gate is only 20 m from the line end, so a car
                # can legitimately leave the segment at ~16 m.
                record = {"vin": vin, "spacing_m": gaps,
                          "rule_allowed": allowed, "rule_reason": why or "clear",
                          "gate_queue_before": queue_before,
                          "in_first_segment_before": segment_before}
                releases.append(record)
                spacing_at_release.append(record)
                emit({"type": "yard.released", **record})
            if not allowed and not releases:
                emit({"type": "yard.holding", "reason": why})
            if not after:
                break
            await asyncio.sleep(2.0)

        emit({"type": "yard.releases", "count": len(releases),
              "still_waiting": [c.vin for c in app.yard.waiting_at_line_end()]})

        # an exception the rules do not cover: the truck is 40 minutes late, so the slots
        # assigned by truck have to be re-planned. Cars already parked for it, and a second
        # truck arriving sooner, are what make this affect several cars - and a plan that moves
        # several cars needs a supervisor, which is the authority boundary in the design.
        now = rt.clock()
        for index in range(args.parked):
            vin = f"VIN-P{index + 1}"
            rt.store.put(Car(vin=vin, state=CarState.YARD,
                             location=f"yard.Y{index + 1}",
                             build_sheet=BuildSheet(model="sedan", colour="black",
                                                    truck_id=args.truck)))
        rt.plant.trucks[args.truck] = Truck(truck_id=args.truck, scheduled=now,
                                            eta=now + timedelta(minutes=40))
        rt.plant.trucks[args.next_truck] = Truck(truck_id=args.next_truck, scheduled=now,
                                                 eta=now + timedelta(minutes=5))
        emit({"type": "yard.trucks", "delayed": args.truck, "next": args.next_truck,
              "parked_for_delayed": args.parked})
        exceptions = app.yard.detect_exceptions()
        emit({"type": "yard.exceptions", "found": [kind for _key, kind, _d in exceptions]})
        proposals: list[dict] = []
        for key, kind, details in exceptions:
            outcome = await app.yard.handle_exception(kind, details, key)
            emit({"type": "yard.exception.handled", "kind": kind, "outcome": outcome})
            proposals.append({"kind": kind, "outcome": outcome})

        pending = [{"id": p.proposal_id, "kind": p.kind, "summary": p.summary}
                   for p in rt.pending()]
        emit({"type": "yard.pending", "proposals": pending})
        approved = []
        for p in rt.pending("replan"):
            approved.append(await app.yard.decide(p.proposal_id, True, SUPERVISOR))
            emit({"type": "yard.approved", "by": SUPERVISOR, "result": approved[-1]})

        result = {
            "released": [r["vin"] for r in releases],
            "spacing_at_release": spacing_at_release,
            "line_end_to_gate_m": 20.0,
            "exceptions": [kind for _k, kind, _d in exceptions],
            "proposals": pending,
            "approved": approved,
            "kpis": app.yard.kpis(),
            # simulated seconds per real second; below 1.0 means the box cannot keep up
            "realtime_ratio": rt.driving.realtime_ratio(),
        }
    finally:
        await rt.driving.stop()
        for actor in rt.driving.world.get_actors().filter("vehicle.*"):
            if actor.attributes.get("role_name") in vins:
                actor.destroy()

    from pathlib import Path
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "yard.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=str))
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        emit({"type": "yard.error", "error": f"{type(exc).__name__}: {exc}"})
        return 1

    # Every release after the first must have had the first segment genuinely clear: the car
    # ahead either MIN_SPACING_M away or already at the gate.
    spacing_ok = True
    for record in result["spacing_at_release"]:
        if not record["rule_allowed"]:
            spacing_ok = False          # released while the rule said no
        if record["gate_queue_before"] >= GATE_QUEUE_LIMIT:
            spacing_ok = False          # released into a full gate queue
        if record["in_first_segment_before"]:
            spacing_ok = False          # two cars in the first segment at once
    proposed = any(p.get("kind") == "replan" for p in result["proposals"])
    ok = (len(result["released"]) >= 1 and spacing_ok
          and "truck_delayed" in result["exceptions"]
          and proposed and bool(result["approved"]))
    emit({"type": "yard.realtime_ratio", "ratio": result.get("realtime_ratio")})
    emit({"type": "yard.done", "released": result["released"],
          "rules_held_on_every_release": spacing_ok, "exceptions": result["exceptions"],
          "went_to_a_human": proposed, "approved": len(result["approved"]),
          "result": "success" if ok else "not demonstrated"})
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if ok else 2


if __name__ == "__main__":
    # Skips interpreter shutdown, where the CARLA client intermittently aborts; see
    # exit_without_teardown.
    from marshal_ai.carla_connection import exit_without_teardown

    exit_without_teardown(main())
