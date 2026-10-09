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
from marshal_ai.records import (BuildSheet, Car, CarReport, CarState, InspectionResult,
                                 Verdict)
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

    # Flow 5 merges the visual and functional results, and merge_verdict returns REVIEW when
    # either is missing. Day 4 is about the functional fault, so the gate result is recorded as
    # a clean pass here (flow 3 on real gate cameras is covered separately). Without it even a
    # healthy car reaches the rework path with nothing to repair.
    rt.store.put(InspectionResult(vin=vin, kind="visual", verdict=Verdict.PASS,
                                  created_at=rt.clock()))

    for actor in rt.driving.world.get_actors().filter("vehicle.*"):
        if actor.attributes.get("role_name") == vin:
            actor.destroy()
    rt.driving.spawn(vin, at="test_track", colour="0,80,200")

    # The waits below end as soon as these records exist. Left in Firestore by an earlier run,
    # they ended them at once: the run reported the old verdicts in seconds, with no frame taken
    # and no Gemini call made. Scene 1 drops its stale records for the same reason.
    for model, key in ((InspectionResult, f"{vin}:functional"), (CarReport, vin)):
        if rt.store.get(model, key) is not None:
            rt.store.delete(model, key)
            emit({"type": "day4.dropped_stale", "record": model.__name__, "key": key})

    await app.functional.start(vin, steps)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while rt.store.get(InspectionResult, f"{vin}:functional") is None:
        if loop.time() > deadline:
            raise TimeoutError(f"{vin}: no functional result within {timeout}s")
        await asyncio.sleep(1.0)

    # The functional result is stored before it is published, and flow 5's finalize makes its
    # own Gemini call to draft the report, so reading proposals straight away finds nothing.
    # Wait for the report the router writes.
    report_deadline = loop.time() + 120.0
    while rt.store.get(CarReport, vin) is None and loop.time() < report_deadline:
        await asyncio.sleep(1.0)
    if rt.store.get(CarReport, vin) is None:
        emit({"type": "flow5.warning", "vin": vin, "error": "no CarReport within 120s"})

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

    # flow 5: confirm the bay while the car is still in the driving system. Confirming after
    # cleanup failed with KeyError because dispatch sends the car a mission to drive there.
    confirmed = []
    for proposal in rt.pending("rework"):
        if proposal.payload.get("vin") != vin:
            continue
        try:
            decision = await app.router.decide(proposal.proposal_id, True, SUPERVISOR)
        except Exception as exc:
            emit({"type": "flow5.confirm_failed", "vin": vin,
                  "error": f"{type(exc).__name__}: {exc}"})
            continue
        confirmed.append(decision)
        emit({"type": "flow5.confirmed", "vin": vin, "by": SUPERVISOR, "decision": decision})
        # let the car actually drive to the bay
        arrive = loop.time() + 180.0
        while loop.time() < arrive:
            car = rt.store.get(Car, vin)
            if car is not None and car.location in {t.bay for t in
                                                   rt.store.get(CarReport, vin).rework}:
                emit({"type": "flow5.arrived", "vin": vin, "bay": car.location})
                break
            await asyncio.sleep(2.0)

    # Remove the car before the next one runs: a finished car left standing on the track
    # blocks the following car's route, which shows up as a mission that never completes.
    for actor in rt.driving.world.get_actors().filter("vehicle.*"):
        if actor.attributes.get("role_name") == vin:
            actor.destroy()
    rt.driving.vehicles.pop(vin, None)
    return {"vin": vin, "verdict": result.verdict.value, "checklist": items,
            "disagreements": len(disagreements), "confirmed": confirmed}


async def run(args: argparse.Namespace) -> dict:
    # The store and bus come from the environment. They were pinned to "memory" here, so the
    # verdicts this scene produces - the dark lamp its telemetry reports as healthy, which is the
    # whole point of it - vanished with the process and could never be looked at afterwards. Set
    # MARSHAL_STORE=gcp to keep them.
    settings = Settings(driving="carla", llm="gemini")
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
    if rt.pending("rework"):
        emit({"type": "flow5.unconfirmed",
              "proposals": [p.proposal_id for p in rt.pending("rework")]})

    reports = {}
    for vin in (args.healthy_vin, args.faulty_vin):
        report = rt.store.get(CarReport, vin)
        if report is not None:
            reports[vin] = {"verdict": report.verdict.value,
                            "bays": [t.bay for t in report.rework],
                            "failed_checks": [i.name for i in report.failed_checks],
                            "summary": (report.summary or "")[:160]}
            car = rt.store.get(Car, vin)
            reports[vin]["car_location"] = car.location if car else None
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
    faulty_report = out["reports"].get(args.faulty_vin, {})
    healthy_report = out["reports"].get(args.healthy_vin, {})
    ok = (healthy["verdict"] == "PASS" and faulty["verdict"] == "FAIL"
          and faulty["disagreements"] >= 1
          and healthy_report.get("verdict") == "PASS"
          and faulty_report.get("verdict") == "FAIL"
          and any("electrical" in b for b in faulty_report.get("bays", [])))
    emit({"type": "day4.done", "healthy": healthy["verdict"], "faulty": faulty["verdict"],
          "faulty_disagreements": faulty["disagreements"],
          "healthy_report": healthy_report.get("verdict"),
          "faulty_report": faulty_report.get("verdict"),
          "faulty_bays": faulty_report.get("bays"),
          "result": "success" if ok else "not demonstrated"})
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0 if ok else 2


if __name__ == "__main__":
    # Skips interpreter shutdown, where the CARLA client intermittently aborts; see
    # exit_without_teardown.
    from marshal_ai.carla_connection import exit_without_teardown

    exit_without_teardown(main())
