"""The five-minute demo script, played end to end.

    python scripts/demo.py                          # fake plant, offline stand-ins for Gemini
    python scripts/demo.py --sim metadrive          # MetaDrive plant + Scenic test lab, on a laptop
    python scripts/demo.py --sim metadrive --view topdown   # ... watch the whole plant live
    python scripts/demo.py --sim metadrive --view 3d        # ... a 3D camera chasing each car
    python scripts/demo.py --sim metadrive --record runs/plant.gif
    python scripts/demo.py --live                   # Gemini for reasoning (yard, reports, scenarios,
                                                    # search); perception stays on ground truth

Scene numbers follow the design doc's demo script.
"""

from __future__ import annotations

import argparse
import asyncio
import textwrap

from marshal_ai.app import MarshalApp, set_app
from marshal_ai.config import Settings
from marshal_ai.demo import BOX_SCENE, seed_demo
from marshal_ai.driving import MissionGo
from marshal_ai.llm import GeminiLLM, RoutedLLM
from marshal_ai.offline import offline_llm
from marshal_ai.records import AssistanceCase, Car, CarReport, CarState, InspectionResult
from marshal_ai.runtime import build_runtime, set_runtime

SUPERVISOR, ENGINEER = "supervisor-ana", "engineer-kim"


def say(text: str = "", indent: int = 2) -> None:
    print(textwrap.indent(textwrap.fill(text, 96), " " * indent) if text else "")


def scene(n: int, title: str) -> None:
    print(f"\n=== {n}. {title} " + "=" * max(0, 80 - len(title)))


async def run_until(app: MarshalApp, done, limit_s: float) -> float:
    """Advance the plant until done() holds; returns the simulated seconds it took."""
    t = 0.0
    while t < limit_s and not done():
        await app.run_for(5)
        t += 5
    return t


async def stuck_car_fake(app: MarshalApp) -> AssistanceCase:
    rt = app.rt
    await rt.missions.send(MissionGo(mission_id="M-demo", vin="VIN-240", waypoints=["yard.Y2"]))
    await app.run_for(8)
    rt.world.scenes["AC-1"] = BOX_SCENE
    await rt.driving.raise_assist("VIN-240", "AC-1", "unknown object in lane",
                                  ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"],
                                  clip_uri="gs://marshal/assist/AC-1.mp4")
    return rt.store.get(AssistanceCase, "AC-1")


async def stuck_car_metadrive(app: MarshalApp) -> AssistanceCase:
    """A real box in VIN-240's lane and a worker at the kerb; the car stops and asks by itself."""
    driving = app.rt.driving
    driving.place_object_in_lane("box-1", "line_end", "inspection_gate", 0.55)
    wx, wy, _ = driving.lane_point("line_end", "inspection_gate", 0.62, lateral=-3.5)
    driving.place_person("worker-1", wx, wy)
    say("A cardboard box falls into the lane before the inspection gate; a worker stands at the kerb.")
    waited = await run_until(app, lambda: app.assistance.open_cases(), 300)
    [case] = app.assistance.open_cases()
    say(f"VIN-240 stopped at the box and asked for help after {waited:.0f} s ({case.case_id}).")
    return case


async def main(live: bool, sim: str, view: str | None, record: str | None) -> None:
    metadrive = sim == "metadrive"
    settings = Settings(bus="memory", store="memory", driving=sim, llm="offline",
                        lab="scenic" if metadrive else "sim", view=view or "")
    llm = offline_llm()
    if live:
        llm = RoutedLLM(default=GeminiLLM(settings.model),
                        routes={"visual.": llm, "track.": llm, "assist.": llm})
    rt = build_runtime(settings, llm=llm, **({"record": record} if metadrive and record else {}))
    app = MarshalApp(rt)
    set_runtime(rt)
    set_app(app)
    if view:                                       # watchable speed-ups of real time
        rt.driving.pace = {"topdown": 4.0, "3d": 2.0}[view]
    say(f"Plant: {sim} driving system; test lab: {app.runner.name}.")

    try:
        scene(1, "Normal flow")
        seed_demo(rt, ["VIN-214"])
        took = await run_until(app, lambda: rt.store.get(Car, "VIN-214").state == CarState.YARD, 900)
        car, report = rt.store.get(Car, "VIN-214"), rt.store.get(CarReport, "VIN-214")
        say(f"VIN-214: released, gate {rt.store.get(InspectionResult, 'VIN-214:visual').verdict.value}, "
            f"track {rt.store.get(InspectionResult, 'VIN-214:functional').verdict.value}, "
            f"report {report.verdict.value}, parked in {car.yard_slot} (nearest the loading bay for T-07) "
            f"after {took:.0f} s.")

        scene(2, "Inspection catches two faults")
        seed_demo(rt, ["VIN-231", "VIN-223"])
        await run_until(app, lambda: len(rt.pending("rework")) >= 2, 900)
        v = rt.store.get(InspectionResult, "VIN-231:visual")
        say(f"VIN-231 gate: {v.verdict.value}: {v.build_sheet_mismatches[0]}")
        f = rt.store.get(InspectionResult, "VIN-223:functional")
        brake = next(i for i in f.checklist if i.name == "brake_lights")
        say(f"VIN-223 track: brake lights telemetry {'ON' if brake.telemetry_ok else 'OFF'}, "
            f"camera {'lit' if brake.camera_ok else 'NOT lit'} -> {f.verdict.value}")
        for p in rt.pending("rework"):
            say(f"{p.proposal_id} {p.summary}. {SUPERVISOR} confirms.")
            await app.router.decide(p.proposal_id, True, SUPERVISOR)
        bays = {"VIN-231": "production_control", "VIN-223": "rework.electrical"}
        await run_until(app, lambda: all(rt.store.get(Car, v).location == b for v, b in bays.items()), 600)
        for vin in bays:
            say(f"{vin} booked into {rt.store.get(Car, vin).location}")

        scene(3, "Stuck car")
        seed_demo(rt, ["VIN-240"])
        case = await (stuck_car_metadrive(app) if metadrive else stuck_car_fake(app))
        say(f"Card: {case.situation}")
        say(f"Recommended: {case.recommended} ({case.confidence:.0%}); "
            f"others: {[(r['option'], r['risk']) for r in case.ranking[1:]]}")
        await app.assistance.decide(case.case_id, case.recommended, SUPERVISOR)
        say(f"{SUPERVISOR} picks {case.recommended}; the case is sent to the test lab.")
        if metadrive:
            await app.run_for(10)
            rt.driving.remove_object("box-1")
            say("The worker picks up the box.")
            await run_until(app, lambda: rt.driving.cars["VIN-240"].x > 45, 120)
            say(f"VIN-240 drives on by itself: now past {rt.driving.cars['VIN-240'].location}.")

        scene(4, "Test lab")
        request = "A worker steps out from behind a parked forklift at dusk while a car turns into the finishing lane."
        say(f'Supervisor types: "{request}"')
        gen = await asyncio.to_thread(app.generator.generate, request)
        if not gen.ok:
            say(f"Generation needs an engineer: {gen.errors}")
            return
        s = gen.scenario.spec
        say(f"{gen.scenario.id}: {s['time_of_day']}, {[a['type'] for a in s['actors']]}, "
            f"criteria {s['pass_criteria']}; compiled ({len(gen.code.splitlines())} lines) and dry-run: "
            f"min gap {gen.dry_run_metrics['min_gap_to_person_m']} m, passes.")
        app.generator.review(gen.scenario.id, True, ENGINEER)
        incident = app.test_requests[-1]
        inc = await asyncio.to_thread(app.generator.generate, incident["text"], incident["source"])
        if inc.ok:
            app.generator.review(inc.scenario.id, True, ENGINEER)
            say(f"Assistance case {case.case_id} became {inc.scenario.id}; {ENGINEER} approves both into "
                f"the library.")
        result = await asyncio.to_thread(app.adversarial.search, gen.scenario.id)
        say(f"Adversarial search: {len(result.runs)} runs. {result.reasoning[-1]}")
        say(result.report())
        if result.minimal:
            say(f"Clip: {result.minimal.run.clip_uri}")

        scene(5, "Close")
        old = await asyncio.to_thread(app.release_gate.evaluate, "behavior-agent-1.0",
                                      app.runner_for("behavior-agent-1.0"))
        say(old.summary())
        new = await asyncio.to_thread(app.release_gate.evaluate, "behavior-agent-1.1",
                                      app.runner_for("behavior-agent-1.1"))
        say(new.summary())
        say('"The AI never drives. It runs the yard, checks every car and tries to break the driving '
            'software before it reaches the plant."')
    finally:
        close = getattr(rt.driving, "close", None)
        if close is not None:
            close()
            if record:
                say(f"Plant recording: {record}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use Gemini for reasoning tasks")
    parser.add_argument("--sim", choices=["fake", "metadrive"], default="fake",
                        help="driving system: instant fake plant, or MetaDrive (+ Scenic test lab)")
    parser.add_argument("--view", choices=["topdown", "3d"], help="MetaDrive: open a live window")
    parser.add_argument("--render", action="store_true", help="same as --view topdown")
    parser.add_argument("--record", help="MetaDrive: save the plant as a GIF, e.g. runs/plant.gif")
    a = parser.parse_args()
    view = a.view or ("topdown" if a.render else None)
    if view and a.sim != "metadrive":
        parser.error("--view needs --sim metadrive")
    asyncio.run(main(a.live, a.sim, view, a.record))
