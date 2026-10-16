#!/usr/bin/env python3
"""Run one calibrated Town05 mission through Marshal's BehaviorAgent backend."""

from __future__ import annotations

import argparse
import asyncio
import json

from marshal_ai.driving import MissionClient, MissionDone, MissionGo, Status
from marshal_ai.driving.carla_backend import CarlaDrivingSystem
from marshal_ai.events import InMemoryBus
from marshal_ai.plant import load_plant_map

ROUTE = ["inspection_gate", "test_track", "report_point", "yard.Y1"]


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--vin", default="VIN-DAY1")
    ap.add_argument("--max-speed-kmh", type=float, default=10.0)
    ap.add_argument("--fixed-delta-s", type=float, default=0.05,
                    help="Simulation step; 0.05 matches BehaviorAgent's 20 Hz PID controller")
    ap.add_argument("--timeout", type=float, default=240.0)
    return ap


async def run(args: argparse.Namespace) -> dict:
    plant_map = load_plant_map()
    if not plant_map.carla_map or not plant_map.carla_map.endswith(("Town05", "Town05_Opt", "OpenDriveMap")):
        raise RuntimeError(f"demo requires Town05, plant map declares {plant_map.carla_map!r}")
    missing = [name for name in ["line_end", *ROUTE] if not plant_map.points[name].carla]
    if missing:
        raise RuntimeError(f"required CARLA transforms are missing: {', '.join(missing)}")

    driving = CarlaDrivingSystem(
        plant_map,
        host=args.host,
        port=args.port,
        fixed_delta_s=args.fixed_delta_s,
        expected_version="0.9.16",
    )
    current_map = driving.world.get_map().name
    if not current_map.endswith(("Town05", "Town05_Opt", "OpenDriveMap")):
        raise RuntimeError(f"server must be on Town05, currently {current_map}")

    driving.despawn(args.vin)                # with its cameras, if an earlier run left it

    bus = InMemoryBus()
    missions = MissionClient(driving, plant_map, bus)
    done = asyncio.Event()
    statuses: list[Status] = []
    result: MissionDone | None = None

    def report(event) -> None:
        nonlocal result
        if isinstance(event, Status):
            statuses.append(event)
            print(json.dumps({
                "type": event.type,
                "vin": event.vin,
                "location": event.location,
                "speed_kmh": round(event.speed_kmh, 2),
                "progress": round(event.progress, 2),
                "x": round(event.x, 2),
                "y": round(event.y, 2),
            }), flush=True)
        elif isinstance(event, MissionDone):
            result = event
            print(event.model_dump_json(), flush=True)
            done.set()

    missions.on_event(report)
    driving.spawn(args.vin, at="line_end", colour="0,80,200")
    try:
        await driving.start()
        await missions.send(MissionGo(
            mission_id="M-DAY1",
            vin=args.vin,
            waypoints=ROUTE,
            max_speed_kmh=args.max_speed_kmh,
        ))
        await asyncio.wait_for(done.wait(), timeout=args.timeout)
    finally:
        await driving.stop()
        # despawn, not actor.destroy(): CARLA keeps a destroyed car's cameras streaming to a client
        # that has gone, which floods the server with "Invalid session" and preceded its crashes.
        for vin in list(driving.vehicles):
            driving.despawn(vin)

    if result is None:
        raise RuntimeError("mission ended without mission.done")
    return {
        "mission_id": result.mission_id,
        "vin": result.vin,
        "location": result.location,
        "status_count": len(statuses),
        "route": ROUTE,
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    result = asyncio.run(run(args))
    print(json.dumps({"result": "success", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
