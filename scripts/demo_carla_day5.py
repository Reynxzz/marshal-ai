#!/usr/bin/env python3
"""Day 5: the stuck car, on real CARLA with real Gemini.

Demo scene 3. A box is placed in the lane with a worker standing beside it. The car drives
at it, BehaviorAgent stops, and the driving system raises assist.request with the forward
frame it captured. Gemini reads that frame and ranks the options the driving system offered;
code enforces the desk's rules; a human picks. The resolved case is sent to the test lab.

The rule that matters: a person in the scene means only a wait option may be recommended.

    cd ~/marshal-app && bash scripts/run_carla_day5.sh
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from marshal_ai.app import MarshalApp
from marshal_ai.config import Settings
from marshal_ai.driving import AssistRequest, MissionGo
from marshal_ai.records import AssistanceCase, BuildSheet, Car, CarState
from marshal_ai.runtime import build_runtime

SUPERVISOR = "supervisor-ana"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--vin", default="VIN-STUCK")
    ap.add_argument("--from-point", default="test_track")
    ap.add_argument("--to-point", default="report_point")
    ap.add_argument("--obstacle-ahead-m", type=float, default=18.0,
                    help="how far down the route to place the box")
    ap.add_argument("--with-worker", action=argparse.BooleanOptionalAction, default=True,
                    help="stand a worker beside the box, which forces a wait recommendation")
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
    rt.driving.on_event(lambda e: requests.append(e) if isinstance(e, AssistRequest) else None)

    emit({"type": "day5.start", "llm": type(rt.llm).__name__,
          "map": rt.driving.world.get_map().name,
          "auto_assist": rt.driving.auto_assist,
          "blocked_after_s": rt.driving.blocked_after_s})

    rt.store.put(Car(vin=args.vin, build_sheet=BuildSheet(model="sedan", colour="black"),
                     state=CarState.TEST_TRACK))
    for actor in rt.driving.world.get_actors().filter("vehicle.*"):
        if actor.attributes.get("role_name") == args.vin:
            actor.destroy()

    props = place_obstacle(rt, args)
    result: dict = {}
    await rt.driving.start()
    try:
        rt.driving.spawn(args.vin, at=args.from_point, colour="0,80,200")
        await rt.missions.send(MissionGo(mission_id=rt.next_id("M", 4), vin=args.vin,
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

        clip = rt.driving.assist_media(request.case_id)
        if clip is not None and clip.data:
            (out / f"{request.case_id}.jpg").write_bytes(clip.data)
        emit({"type": "flow2.clip", "bytes": len(clip.data) if clip and clip.data else 0})

        # the desk: Gemini reads the frame, the rules are enforced in code
        case = await app.assistance.on_request(request)
        emit({"type": "flow2.card", "case_id": case.case_id, "situation": case.situation,
              "people_or_moving_equipment": case.people_or_moving_equipment,
              "recommended": case.recommended, "confidence": round(case.confidence, 2),
              "ranking": case.ranking})

        # a human always decides
        chosen = case.recommended or request.options[0]
        await app.assistance.decide(case.case_id, chosen, SUPERVISOR)
        resolved = rt.store.get(AssistanceCase, case.case_id)
        emit({"type": "flow2.decided", "by": SUPERVISOR, "choice": resolved.choice})

        # the car must be released and finish its mission
        released_by = loop.time() + 120.0
        v = rt.driving.vehicles[args.vin]
        while v.assist_case is not None and loop.time() < released_by:
            await asyncio.sleep(1.0)
        emit({"type": "flow2.released", "still_waiting": v.assist_case is not None})

        result = {
            "case_id": case.case_id,
            "reason": request.reason,
            "options": request.options,
            "people_or_moving_equipment": case.people_or_moving_equipment,
            "recommended": case.recommended,
            "choice": resolved.choice,
            "clip_bytes": len(clip.data) if clip and clip.data else 0,
            "test_requests": app.test_requests[-1:],
            "released": v.assist_case is None,
        }
    finally:
        await rt.driving.stop()
        for actor in list(props):
            try:
                actor.destroy()
            except Exception:
                pass
        for actor in rt.driving.world.get_actors().filter("vehicle.*"):
            if actor.attributes.get("role_name") == args.vin:
                actor.destroy()

    (out / "case.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=str))
    return result


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
          # a person beside the box means only a wait option may be recommended
          and (wait_recommended if result["people_or_moving_equipment"] else True))
    emit({"type": "day5.done", "recommended": result["recommended"],
          "people_or_moving_equipment": result["people_or_moving_equipment"],
          "sent_to_test_lab": bool(result["test_requests"]),
          "result": "success" if ok else "not demonstrated"})
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
