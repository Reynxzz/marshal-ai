#!/usr/bin/env python3
"""Day 4: the faulty-car case, on real CARLA with real Gemini.

Demo scene 2 needs a car whose electronics report a lamp as working while the camera
shows it dark. Everything for that already existed separately; this runs it together:

    flow 4  CarlaDrivingSystem drives routine R-12. Telemetry is what the car reports,
            which stays healthy even for a dark lamp (`faults` keeps the light bit off).
            Gemini judges each real track frame. An item passes only when both agree.
    flow 5  The Report and Rework Router merges the result, writes the report and
            proposes a rework bay for a supervisor to confirm.

Two cars run: one healthy, one with a dark lamp. The healthy car must PASS and the
faulty one must FAIL on a telemetry/camera disagreement, which is the whole point of
the check. Run it on the VM:

    cd ~/marshal-app && bash scripts/run_carla_day4.sh
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from marshal_ai.app import MarshalApp
from marshal_ai.config import Settings
from marshal_ai.records import BuildSheet, Car, CarReport, CarState, InspectionResult
from marshal_ai.runtime import build_runtime

SUPERVISOR = "supervisor-ana"

# headlights is the only step Gemini confirms reliably from a still (front view, lit road);
# brake reads as a running light on this vehicle and a blinker needs a clip, so a dark-lamp
# demo built on those would be ambiguous rather than convincing.
FAULT_STEP = "headlights"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--healthy-vin", default="VIN-OK")
    ap.add_argument("--faulty-vin", default="VIN-DARK")
    ap.add_argument("--fault-step", default=FAULT_STEP)
    ap.add_argument("--steps", default="1", help="routine steps to run, comma separated")
    ap.add_argument("--sun-altitude", type=float, default=-8.0,
                    help="lamps do not read at noon; a low sun is what makes the camera half work")
    ap.add_argument("--timeout", type=float, default=300.0)
    return ap


def emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True, default=str), flush=True)


async def check_one(app: MarshalApp, vin: str, steps: list[int], fault: str | None,
                    timeout: float) -> dict:
    rt = app.rt
    rt.store.put(Car(vin=vin, build_sheet=BuildSheet(model="sedan", colour="black"),
                     state=CarState.TEST_TRACK))
    rt.driving.faults[vin] = {fault} if fault else set()

    for actor in rt.driving.world.get_actors().filter("vehicle.*"):
        if actor.attributes.get("role_name") == vin:
            actor.destroy()
    rt.driving.spawn(vin, at="test_track", colour="0,80,200")

    await app.functional.start(vin, steps)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while rt.store.get(InspectionResult, f"{vin}:functional") is None:
        if loop.time() > deadline:
            raise TimeoutError(f"{vin}: no functional result within {timeout}s")
        await asyncio.sleep(1.0)

    result = rt.store.get(InspectionResult, f"{vin}:functional")
    # Keep the frames the verdict was based on, so a disputed call can be checked by eye.
    run_clips = getattr(app.functional, "_last_clips", {})
    for step_no, media in run_clips.get(vin, {}).items():
        if media.data:
            out_path = Path("/tmp/marshal-day4") / f"{vin}_step{step_no}.jpg"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(media.data)
    items = [{"step": i.step, "name": i.name, "telemetry_ok": i.telemetry_ok,
              "camera_ok": i.camera_ok, "camera_confidence": round(i.camera_confidence or 0, 2),
              "passed": i.passed} for i in result.checklist]
    disagreements = [i for i in result.checklist
                     if i.telemetry_ok is not None and i.camera_ok is not None
                     and i.telemetry_ok != i.camera_ok]
    emit({"type": "flow4.result", "vin": vin, "fault": fault, "verdict": result.verdict.value,
          "checklist": items, "telemetry_camera_disagreements": len(disagreements)})

    # Remove the car before the next one runs: a finished car left standing on the track
    # blocks the following car's route, which shows up as a mission that never completes.
    for actor in rt.driving.world.get_actors().filter("vehicle.*"):
        if actor.attributes.get("role_name") == vin:
            actor.destroy()
    rt.driving.vehicles.pop(vin, None)
    return {"vin": vin, "verdict": result.verdict.value, "checklist": items,
            "disagreements": len(disagreements)}


async def run(args: argparse.Namespace) -> dict:
    settings = Settings(driving="carla", llm="gemini", bus="memory", store="memory")
    rt = build_runtime(settings, host=args.host, port=args.port)
    app = MarshalApp(rt)
    if type(rt.llm).__name__ != "GeminiLLM":
        raise RuntimeError(f"expected GeminiLLM, got {type(rt.llm).__name__}; set MARSHAL_LLM=gemini")

    weather = rt.driving.world.get_weather()
    weather.sun_altitude_angle = args.sun_altitude
    rt.driving.world.set_weather(weather)
    emit({"type": "day4.start", "llm": type(rt.llm).__name__,
          "track_camera": type(rt.track_camera).__name__,
          "map": rt.driving.world.get_map().name, "sun_altitude": args.sun_altitude})

    steps = [int(s) for s in args.steps.split(",") if s.strip()]
    out: dict = {}
    await rt.driving.start()
    try:
        out["healthy"] = await check_one(app, args.healthy_vin, steps, None, args.timeout)
        out["faulty"] = await check_one(app, args.faulty_vin, steps, args.fault_step, args.timeout)
    finally:
        await rt.driving.stop()
        for actor in rt.driving.world.get_actors().filter("vehicle.*"):
            if actor.attributes.get("role_name") in (args.healthy_vin, args.faulty_vin):
                actor.destroy()

    # flow 5: the router should have proposed a bay for the faulty car
    pending = [{"id": p.proposal_id, "kind": p.kind, "summary": p.summary}
               for p in rt.pending()]
    emit({"type": "flow5.pending", "proposals": pending})
    for proposal in rt.pending("rework"):
        decision = await app.router.decide(proposal.proposal_id, True, SUPERVISOR)
        emit({"type": "flow5.confirmed", "by": SUPERVISOR, "decision": decision})

    reports = {}
    for vin in (args.healthy_vin, args.faulty_vin):
        report = rt.store.get(CarReport, vin)
        if report is not None:
            reports[vin] = {"verdict": report.verdict.value, "rework_bay": report.rework_bay,
                            "summary": report.summary}
    emit({"type": "flow5.reports", "reports": reports})
    out["reports"] = reports
    out["kpis"] = app.functional.kpis()
    return out


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        out = asyncio.run(run(args))
    except Exception as exc:
        emit({"type": "day4.error", "error": f"{type(exc).__name__}: {exc}"})
        return 1

    healthy, faulty = out["healthy"], out["faulty"]
    ok = (healthy["verdict"] == "PASS" and faulty["verdict"] == "FAIL"
          and faulty["disagreements"] >= 1)
    emit({"type": "day4.done", "healthy": healthy["verdict"], "faulty": faulty["verdict"],
          "faulty_disagreements": faulty["disagreements"],
          "result": "success" if ok else "not demonstrated"})
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
