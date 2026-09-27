#!/usr/bin/env python3
"""Demo scene 1, as separate phases: a car leaves the line, passes the gate and the track
routine, and gets a clean report.

Each phase is its own process because a camera capture and a running driving loop cannot
coexist: inline the loop is the only ticker and is blocked waiting for the capture, so no frame
arrives; concurrently the CARLA server segfaults ("Waited for 10 seconds on IO....Signal 11").
So a phase either drives or looks, never both.

    release  flow 1  driving loop, no cameras     line_end -> inspection_gate
    gate     flow 3  cameras, no driving loop     four views, Gemini verdict
    track    flow 4  Day 2's pattern              R-12 lighting, camera + telemetry
    report   flow 5  driving loop, no cameras     merge, then drive to a yard slot

The phases share state through Firestore (MARSHAL_STORE=gcp), which is what makes the decision
chain genuinely end to end across processes. The car itself is re-spawned at each phase's start
point, because a vehicle cannot be handed between processes; the records carry over, the physical
car does not.

    cd ~/marshal-app && bash scripts/run_carla_scene1.sh
"""

from __future__ import annotations

import argparse
import asyncio
import json

from marshal_ai.app import MarshalApp
from marshal_ai.config import Settings
from marshal_ai.records import (BuildSheet, Car, CarReport, CarState, InspectionResult,
                                Verdict)
from marshal_ai.runtime import build_runtime

SUPERVISOR = "supervisor-ana"
PHASES = ("release", "gate", "track", "report")
# The car is spawned in this colour and the build sheet says the same, because scene 1 is the
# clean-report narrative. Flow 3 does catch a mismatch: with the sheet set to black it reported
# "colour: build sheet says black, car shows blue (1.00)" and failed the car, which is scene 2.
CAR_COLOUR = "0,80,200"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", required=True, choices=PHASES)
    ap.add_argument("--vin", default="VIN-S1")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--truck", default="T-1")
    ap.add_argument("--timeout", type=float, default=300.0)
    return ap


def emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True, default=str), flush=True)


def build(args):
    rt = build_runtime(Settings(driving="carla", llm="gemini"), host=args.host, port=args.port)
    app = MarshalApp(rt)
    if type(rt.llm).__name__ != "GeminiLLM":
        raise RuntimeError(f"expected GeminiLLM, got {type(rt.llm).__name__}")
    if rt.settings.store != "gcp":
        raise RuntimeError("scene 1 needs MARSHAL_STORE=gcp so the phases share records")
    return rt, app


def respawn(rt, vin: str, at: str, colour: str = CAR_COLOUR) -> None:
    for actor in rt.driving.world.get_actors().filter("vehicle.*"):
        if actor.attributes.get("role_name") == vin:
            actor.destroy()
    rt.driving.spawn(vin, at=at, colour=colour)


async def wait_for(check, timeout: float, what: str):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = check()
        if value:                     # not "is not None": the checks return False while waiting
            return value
        await asyncio.sleep(1.0)
    raise TimeoutError(f"{what} did not happen within {timeout:.0f}s")


# --------------------------------------------------------------------------- #

def only_this_flow(app, *, gate: bool = False) -> None:
    """Stop one phase's events chaining into the next flow.

    Arrival at the gate makes the app start flow 3 inside the driving loop, where the capture
    cannot work; worse, the failure aborts the whole mission.done handler, so the car's arrival
    was never recorded and the phase waited forever. Each phase runs one flow and the report
    phase merges, so the chain is cut explicitly.
    """
    if not gate:
        async def _skip(vin, views=None, check_variant=True, images=None):
            emit({"type": "scene1.chain_suppressed", "flow": "visual", "vin": vin})
        app.visual.inspect = _skip

    async def _skip_result(result):
        emit({"type": "scene1.chain_suppressed", "flow": "report", "result": result.kind})
    app.router.on_result = _skip_result


async def phase_release(rt, app, args) -> dict:
    """Flow 1. A driving loop and no cameras."""
    only_this_flow(app)
    rt.store.put(Car(vin=args.vin, state=CarState.LINE_END, location="line_end",
                     build_sheet=BuildSheet(model="sedan", colour="blue",
                                            truck_id=args.truck)))
    respawn(rt, args.vin, "line_end")
    await rt.driving.start()
    try:
        released = await app.yard.release_next()
        emit({"type": "scene1.released", "vin": released,
              "rule": app.yard.can_release()[1] or "clear"})
        if released != args.vin:
            raise RuntimeError(f"yard released {released!r}, expected {args.vin}")
        car = await wait_for(
            lambda: (c := rt.store.get(Car, args.vin)) and c.location == "inspection_gate" and c,
            args.timeout, "arrival at the inspection gate")
        emit({"type": "scene1.at_gate", "vin": car.vin, "state": car.state.value})
        return {"released": released, "location": car.location,
                "realtime_ratio": rt.driving.realtime_ratio()}
    finally:
        await rt.driving.stop()
        respawn_cleanup(rt, args.vin)


def _day2_module():
    """Load the day-2 script as a module, to reuse its camera.

    Its _FixedEvidenceCamera is the only gate capture that has never crashed the server. The
    equivalent path in the backend segfaults CARLA on camera creation, so this reuses the code
    that works rather than a reimplementation of it.
    """
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("demo_carla_day2.py")
    spec = importlib.util.spec_from_file_location("demo_carla_day2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Held for the life of the process. The camera is deliberately never destroyed - destroying it
# is where the server times out and segfaults - so a reference has to survive, or garbage
# collection tries the cleanup instead and the client is closed underneath the run.
_HELD: list = []


def capture_gate_views(rt, vin: str) -> list:
    """Four gate views, using day 2's camera, returned as Media with real bytes."""
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from marshal_ai.driving.carla_backend import GATE_VIEWS
    from marshal_ai.llm import Media

    day2 = _day2_module()
    camera = day2._FixedEvidenceCamera(rt.driving)
    _HELD.extend([day2, camera])
    views = list(GATE_VIEWS)
    media: list[Media] = []
    with TemporaryDirectory() as tmp:
        camera.start(day2._gate_transform(rt.driving, views[0]))
        for view in views:
            path = Path(tmp) / f"{view}.jpg"
            camera.capture_at(day2._gate_transform(rt.driving, view), path)
            data = path.read_bytes()
            uri = rt.driving.store_artifact(f"gs://marshal/gate/{vin}/{view}.jpg", data)
            media.append(Media(mime="image/jpeg", data=data, label=f"{view} camera", uri=uri))
    # The sensor is deliberately not stopped or destroyed. Destroying it is where the server
    # times out and then segfaults; the frames are already in hand, and process exit drops the
    # connection, which CARLA handles. Nothing after this point needs the simulator.
    return media


async def phase_gate(rt, app, args) -> dict:
    """Flow 3. Cameras and no driving loop, which is the only combination that works."""
    only_this_flow(app, gate=True)
    respawn(rt, args.vin, "inspection_gate")
    try:
        images = capture_gate_views(rt, args.vin)
        emit({"type": "scene1.gate_captured",
              "views": [(m.label.split()[0], len(m.data)) for m in images]})
        result = await app.visual.inspect(args.vin, None, True, images=images)
        emit({"type": "scene1.gate", "vin": args.vin, "verdict": result.verdict.value,
              "findings": [(f.zone, f.type, round(f.confidence, 2)) for f in result.findings],
              "mismatches": list(result.build_sheet_mismatches)})
        return {"verdict": result.verdict.value,
                "findings": len(result.findings),
                "mismatches": len(result.build_sheet_mismatches)}
    finally:
        # No sensor or vehicle teardown here, for the reason in capture_gate_views. The next
        # phase is a new process and clears anything left behind before it spawns.
        pass


async def phase_track(rt, app, args) -> dict:
    """Flow 4. Cameras created before the loop starts, as Day 2 does."""
    only_this_flow(app)
    rt.track_camera.prewarm()
    respawn(rt, args.vin, "test_track")
    await rt.driving.start()
    try:
        await app.functional.start(args.vin, [1])
        result = await wait_for(
            lambda: rt.store.get(InspectionResult, f"{args.vin}:functional"),
            args.timeout, "the functional result")
        items = [{"name": i.name, "telemetry_ok": i.telemetry_ok, "camera_ok": i.camera_ok,
                  "passed": i.passed} for i in result.checklist]
        emit({"type": "scene1.track", "vin": args.vin, "verdict": result.verdict.value,
              "checklist": items})
        return {"verdict": result.verdict.value, "checklist": items,
                "realtime_ratio": rt.driving.realtime_ratio()}
    finally:
        await rt.driving.stop()
        respawn_cleanup(rt, args.vin)


async def phase_report(rt, app, args) -> dict:
    """Flow 5. Merge both results, then drive the car to its slot."""
    visual = rt.store.get(InspectionResult, f"{args.vin}:visual")
    functional = rt.store.get(InspectionResult, f"{args.vin}:functional")
    if visual is None or functional is None:
        raise RuntimeError("scene 1 needs both the gate and track results; run those phases first")

    respawn(rt, args.vin, "report_point")
    await rt.driving.start()
    try:
        report = await app.router.finalize(args.vin)
        emit({"type": "scene1.report", "vin": args.vin, "verdict": report.verdict.value,
              "summary": (report.summary or "")[:160],
              "failed_checks": [i.name for i in report.failed_checks]})
        for proposal in rt.pending("rework"):
            emit({"type": "scene1.rework_proposed", "summary": proposal.summary})
        if report.verdict is Verdict.PASS:
            car = await wait_for(
                lambda: (c := rt.store.get(Car, args.vin)) and c.state is CarState.YARD and c,
                args.timeout, "arrival in the yard")
            emit({"type": "scene1.in_yard", "vin": car.vin, "slot": car.location})
        final = rt.store.get(CarReport, args.vin)
        car = rt.store.get(Car, args.vin)
        return {"verdict": final.verdict.value, "state": car.state.value,
                "location": car.location, "summary": (final.summary or "")[:200]}
    finally:
        await rt.driving.stop()
        respawn_cleanup(rt, args.vin)


def respawn_cleanup(rt, vin: str) -> None:
    for actor in rt.driving.world.get_actors().filter("vehicle.*"):
        if actor.attributes.get("role_name") == vin:
            try:
                actor.destroy()
            except Exception:
                pass
    rt.driving.vehicles.pop(vin, None)


RUNNERS = {"release": phase_release, "gate": phase_gate,
           "track": phase_track, "report": phase_report}


async def run(args) -> dict:
    rt, app = build(args)
    emit({"type": "scene1.phase.start", "phase": args.phase, "vin": args.vin,
          "map": rt.driving.world.get_map().name, "store": rt.settings.store})
    return await RUNNERS[args.phase](rt, app, args)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        out = asyncio.run(run(args))
    except Exception as exc:
        emit({"type": "scene1.phase.error", "phase": args.phase,
              "error": f"{type(exc).__name__}: {exc}"})
        return 1
    emit({"type": "scene1.phase.done", "phase": args.phase, **out})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
