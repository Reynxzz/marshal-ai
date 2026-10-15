#!/usr/bin/env python3
"""Day 5: the stuck car, on real CARLA with real Gemini.

Demo scene 3. A box is placed in the lane with a worker standing beside it. The car drives
at it, BehaviorAgent stops, and the driving system raises assist.request with the forward
frame it captured. Gemini reads that frame and ranks the options the driving system offered;
code enforces the desk's rules; a human picks. The resolved case is sent to the test lab.

The rule that matters: a person in the scene means only a wait option may be recommended.

Then the car does what was chosen, and the run reports how it got to its stop: waiting until the
box and the worker are cleared away, passing in the left lane at 3 km/h, or rerouting through
lane C. The car starts at buffer zone B and the box stands on the test track mark, so there is
room to pull out round it and back in before the report point; the options a car is offered are
only the ones it can drive where it stands.

    cd ~/marshal-app && bash scripts/run_carla_day5.sh
    bash scripts/run_carla_day5.sh --no-with-worker --choose pass_left_3kmh
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from marshal_ai.app import MarshalApp
from marshal_ai.config import Settings
from marshal_ai.driving import AssistRequest, MissionDone, MissionGo
from marshal_ai.records import AssistanceCase, BuildSheet, Car, CarState
from marshal_ai.runtime import build_runtime

SUPERVISOR = "supervisor-ana"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--vin", default="VIN-STUCK")
    ap.add_argument("--from-point", default="buffer_zone_b")
    ap.add_argument("--to-point", default="report_point")
    ap.add_argument("--obstacle-ahead-m", type=float, default=26.0,
                    help="how far down the route to place the box (26 m: on the test track mark)")
    ap.add_argument("--with-worker", action=argparse.BooleanOptionalAction, default=True,
                    help="stand a worker beside the box, which forces a wait recommendation")
    ap.add_argument("--choose", default=None,
                    help="the supervisor's choice; default: the desk's recommendation")
    ap.add_argument("--clear-after-s", type=float, default=10.0,
                    help="when waiting, the box and the worker are cleared away after this long")
    ap.add_argument("--sun-altitude", type=float, default=45.0)
    ap.add_argument("--output", default="/tmp/marshal-day5")
    ap.add_argument("--timeout", type=float, default=300.0)
    return ap


def emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True, default=str), flush=True)


def place_obstacle(rt, args) -> list:
    """A box in the lane and, optionally, a worker beside it."""
    import carla

    world = rt.driving.world
    lib = world.get_blueprint_library()
    base = rt.driving.transform(args.from_point)
    forward, right = base.get_forward_vector(), base.get_right_vector()
    spawned = []

    def at(ahead: float, side: float, z: float = 0.4):
        loc = base.location + forward * ahead + right * side
        loc.z += z
        return carla.Transform(loc, carla.Rotation(yaw=base.rotation.yaw))

    box_bp = next((b for b in lib.filter("static.prop.box*")), None) or \
        next(iter(lib.filter("static.prop.*")))
    box = world.try_spawn_actor(box_bp, at(args.obstacle_ahead_m, 0.0))
    if box is None:                      # props can refuse a pose; nudge it sideways
        box = world.try_spawn_actor(box_bp, at(args.obstacle_ahead_m, 0.6))
    if box is not None:
        spawned.append(box)
        emit({"type": "day5.obstacle", "actor": box.type_id,
              "ahead_m": args.obstacle_ahead_m})

    if args.with_worker:
        walker_bp = next(iter(lib.filter("walker.pedestrian.*")))
        worker = world.try_spawn_actor(walker_bp, at(args.obstacle_ahead_m + 1.0, 2.6, 0.8))
        if worker is not None:
            spawned.append(worker)
            emit({"type": "day5.worker", "actor": worker.type_id, "beside_box_m": 2.6})
    return spawned


async def run(args: argparse.Namespace) -> dict:
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    rt = build_runtime(Settings(driving="carla", llm="gemini", bus="memory", store="memory"),
                       host=args.host, port=args.port)
    app = MarshalApp(rt)
    if type(rt.llm).__name__ != "GeminiLLM":
        raise RuntimeError(f"expected GeminiLLM, got {type(rt.llm).__name__}")

    weather = rt.driving.world.get_weather()
    weather.sun_altitude_angle = args.sun_altitude
    rt.driving.world.set_weather(weather)

    requests: list[AssistRequest] = []
    done: list[MissionDone] = []
    rt.driving.on_event(lambda e: requests.append(e) if isinstance(e, AssistRequest) else None)
    rt.driving.on_event(lambda e: done.append(e) if isinstance(e, MissionDone) else None)

    emit({"type": "day5.start", "llm": type(rt.llm).__name__,
          "map": rt.driving.world.get_map().name,
          "auto_assist": rt.driving.auto_assist,
          "blocked_after_s": rt.driving.blocked_after_s})

    rt.store.put(Car(vin=args.vin, build_sheet=BuildSheet(model="sedan", colour="black"),
                     state=CarState.TEST_TRACK))
    rt.driving.despawn(args.vin)             # with its cameras, if an earlier run left it

    props = place_obstacle(rt, args)
    result: dict = {}
    await rt.driving.start()
    try:
        rt.driving.spawn(args.vin, at=args.from_point, colour="0,80,200")
        mission_id = rt.next_id("M", 4)
        await rt.missions.send(MissionGo(mission_id=mission_id, vin=args.vin,
                                        waypoints=[args.to_point], max_speed_kmh=8.0))

        loop = asyncio.get_event_loop()
        deadline = loop.time() + args.timeout
        while not requests and loop.time() < deadline:
            await asyncio.sleep(1.0)
        if not requests:
            raise TimeoutError("the car never asked for help; it was not blocked")

        request = requests[0]
        emit({"type": "flow2.request", "case_id": request.case_id, "vin": request.vin,
              "reason": request.reason, "options": request.options})

        # The design asks for a 10 s clip with the request, so this is a run of frames now, not
        # one still. All of them are written out: the desk's recommendation should be checkable
        # against the same evidence it was given.
        clip = rt.driving.assist_media(request.case_id)
        for i, frame in enumerate(clip, start=1):
            if frame.data:
                (out / f"{request.case_id}_f{i}.jpg").write_bytes(frame.data)
        emit({"type": "flow2.clip", "frames": len(clip),
              "bytes": sum(len(f.data or b"") for f in clip)})

        # the desk: Gemini reads the frame, the rules are enforced in code
        case = await app.assistance.on_request(request)
        emit({"type": "flow2.card", "case_id": case.case_id, "situation": case.situation,
              "people_or_moving_equipment": case.people_or_moving_equipment,
              "recommended": case.recommended, "confidence": round(case.confidence, 2),
              "ranking": case.ranking})

        # a human always decides
        chosen = args.choose or case.recommended or request.options[0]
        if chosen not in request.options:
            raise ValueError(f"{chosen} was not offered here; offered {request.options}")
        await app.assistance.decide(case.case_id, chosen, SUPERVISOR)
        resolved = rt.store.get(AssistanceCase, case.case_id)
        emit({"type": "flow2.decided", "by": SUPERVISOR, "choice": resolved.choice,
              "recommended": case.recommended})

        # the car must be released, do what was chosen, and finish its mission
        v = rt.driving.vehicles[args.vin]
        emit({"type": "flow2.released", "still_waiting": v.assist_case is not None})
        cleared_at = loop.time() + args.clear_after_s
        finish_by = loop.time() + 180.0
        while not any(d.mission_id == mission_id for d in done) and loop.time() < finish_by:
            if props and chosen.startswith("wait") and loop.time() >= cleared_at:
                for actor in props:
                    actor.destroy()
                props.clear()
                emit({"type": "day5.cleared", "after_s": args.clear_after_s})
            await asyncio.sleep(0.5)
        reached = any(d.mission_id == mission_id for d in done)
        x, y = rt.driving.position_xy(args.vin)
        tx, ty = rt.driving.reference_xy(args.to_point)
        outcome = rt.driving.assist_outcomes.get(case.case_id, {})
        emit({"type": "flow2.executed", "reached": reached, "at": args.to_point if reached else None,
              "off_mark_m": round(((x - tx) ** 2 + (y - ty) ** 2) ** 0.5, 2), "outcome": outcome})

        result = {
            "case_id": case.case_id,
            "reason": request.reason,
            "options": request.options,
            "people_or_moving_equipment": case.people_or_moving_equipment,
            "recommended": case.recommended,
            "choice": resolved.choice,
            "clip_frames": len(clip),
            "clip_bytes": sum(len(f.data or b"") for f in clip),
            "test_requests": app.test_requests[-1:],
            "released": v.assist_case is None,
            "reached": reached,
            "outcome": outcome,
        }
    finally:
        await rt.driving.stop()
        for actor in list(props):
            try:
                actor.destroy()
            except Exception:
                pass
        # despawn, not actor.destroy(): CARLA keeps a destroyed car's cameras streaming to a client
        # that has gone, which floods the server with "Invalid session" and preceded its crashes.
        rt.driving.despawn(args.vin)

    (out / "case.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=str))
    return result


def _did(outcome: dict, choice: str) -> bool:
    if choice.startswith("wait"):
        return outcome.get("executed") == "waited"
    result = outcome.get("result") or ""
    return (outcome.get("executed") == choice and not outcome.get("contacts")
            and (result == "back in lane" or result.startswith("arrived")))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        emit({"type": "day5.error", "error": f"{type(exc).__name__}: {exc}"})
        return 1

    wait_recommended = (result["recommended"] or "").lower().startswith("wait")
    ok = (result["clip_bytes"] > 0
          and result["choice"] in result["options"]
          and bool(result["test_requests"])
          and result["released"]
          # and it did what was chosen, without touching anything, and got where it was going
          and result["reached"]
          and _did(result["outcome"], result["choice"])
          # a person beside the box means only a wait option may be recommended
          and (wait_recommended if result["people_or_moving_equipment"] else True))
    emit({"type": "day5.done", "recommended": result["recommended"],
          "people_or_moving_equipment": result["people_or_moving_equipment"],
          "sent_to_test_lab": bool(result["test_requests"]),
          "choice": result["choice"], "reached": result["reached"],
          "executed": result["outcome"].get("executed"),
          "result": "success" if ok else "not demonstrated"})
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if ok else 2


if __name__ == "__main__":
    # Skips interpreter shutdown, where the CARLA client intermittently aborts; see
    # exit_without_teardown.
    from marshal_ai.carla_connection import exit_without_teardown

    exit_without_teardown(main())
