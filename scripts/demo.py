"""The five-minute demo script, played end to end on the fake plant.

    python scripts/demo.py            # offline: deterministic stand-ins for Gemini
    python scripts/demo.py --live     # Gemini for reasoning (yard, reports, scenarios, search);
                                      # perception stays on the fake world's ground truth

Scene numbers follow the design doc's demo script.
"""

from __future__ import annotations

import argparse
import asyncio
import textwrap

from marshal_ai.app import RELEASES, MarshalApp, set_app
from marshal_ai.config import Settings
from marshal_ai.demo import BOX_SCENE, seed_demo
from marshal_ai.driving import MissionGo
from marshal_ai.lab.runner import SimRunner
from marshal_ai.llm import GeminiLLM, RoutedLLM
from marshal_ai.offline import offline_llm
from marshal_ai.records import AssistanceCase, Car, CarReport, InspectionResult
from marshal_ai.runtime import build_runtime, set_runtime

SUPERVISOR, ENGINEER = "supervisor-ana", "engineer-kim"


def say(text: str = "", indent: int = 2) -> None:
    print(textwrap.indent(textwrap.fill(text, 96), " " * indent) if text else "")


def scene(n: int, title: str) -> None:
    print(f"\n=== {n}. {title} " + "=" * max(0, 80 - len(title)))


async def main(live: bool) -> None:
    settings = Settings(bus="memory", store="memory", driving="fake", llm="offline")
    llm = offline_llm()
    if live:
        llm = RoutedLLM(default=GeminiLLM(settings.model),
                        routes={"visual.": llm, "track.": llm, "assist.": llm})
    rt = build_runtime(settings, llm=llm)
    app = MarshalApp(rt)
    set_runtime(rt)
    set_app(app)

    scene(1, "Normal flow")
    seed_demo(rt, ["VIN-214"])
    await app.run_for(320)
    car, report = rt.store.get(Car, "VIN-214"), rt.store.get(CarReport, "VIN-214")
    say(f"VIN-214: released, gate {rt.store.get(InspectionResult, 'VIN-214:visual').verdict.value}, "
        f"track {rt.store.get(InspectionResult, 'VIN-214:functional').verdict.value}, "
        f"report {report.verdict.value}, parked in {car.yard_slot} (nearest the loading bay for T-07).")

    scene(2, "Inspection catches two faults")
    seed_demo(rt, ["VIN-231", "VIN-223"])
    await app.run_for(360)
    v = rt.store.get(InspectionResult, "VIN-231:visual")
    say(f"VIN-231 gate: {v.verdict.value}: {v.build_sheet_mismatches[0]}")
    f = rt.store.get(InspectionResult, "VIN-223:functional")
    brake = next(i for i in f.checklist if i.name == "brake_lights")
    say(f"VIN-223 track: brake lights telemetry {'ON' if brake.telemetry_ok else 'OFF'}, "
        f"camera {'lit' if brake.camera_ok else 'NOT lit'} -> {f.verdict.value}")
    for p in rt.pending("rework"):
        say(f"{p.proposal_id} {p.summary}. {SUPERVISOR} confirms.")
        await app.router.decide(p.proposal_id, True, SUPERVISOR)
    await app.run_for(120)
    for vin in ("VIN-231", "VIN-223"):
        say(f"{vin} booked into {rt.store.get(Car, vin).location}")

    scene(3, "Stuck car")
    seed_demo(rt, ["VIN-240"])
    await rt.missions.send(MissionGo(mission_id="M-demo", vin="VIN-240", waypoints=["yard.Y2"]))
    await app.run_for(8)
    rt.world.scenes["AC-1"] = BOX_SCENE
    await rt.driving.raise_assist("VIN-240", "AC-1", "unknown object in lane",
                                  ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"],
                                  clip_uri="gs://marshal/assist/AC-1.mp4")
    case = rt.store.get(AssistanceCase, "AC-1")
    say(f"Card: {case.situation}")
    say(f"Recommended: {case.recommended} ({case.confidence:.0%}); "
        f"others: {[(r['option'], r['risk']) for r in case.ranking[1:]]}")
    await app.assistance.decide("AC-1", case.recommended, SUPERVISOR)
    say(f"{SUPERVISOR} picks {case.recommended}; the case is sent to the test lab.")

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
        say(f"Assistance case AC-1 became {inc.scenario.id}; {ENGINEER} approves both into the library.")
    result = await asyncio.to_thread(app.adversarial.search, gen.scenario.id)
    say(f"Adversarial search: {len(result.runs)} runs. {result.reasoning[-1]}")
    say(result.report())
    if result.minimal:
        say(f"Clip: {result.minimal.run.clip_uri}")

    scene(5, "Close")
    old = app.release_gate.evaluate("behavior-agent-1.0", SimRunner(rt.map, RELEASES["behavior-agent-1.0"]))
    say(old.summary())
    new = app.release_gate.evaluate("behavior-agent-1.1", SimRunner(rt.map, RELEASES["behavior-agent-1.1"]))
    say(new.summary())
    say('"The AI never drives. It runs the yard, checks every car and tries to break the driving '
        'software before it reaches the plant."')


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use Gemini for reasoning tasks")
    asyncio.run(main(parser.parse_args().live))
