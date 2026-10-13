#!/usr/bin/env python3
"""How often the gate fails a clean car, under different suns.

A clean, correctly specified car is parked on the gate mark and inspected repeatedly with real Gemini
under each sun position. Anything other than PASS is a false alarm, since the car has no damage and
matches its build sheet. Records go to a throwaway in-memory store.

    python3 scripts/probe_gate_lighting.py --suns 15:default,15:road,30:road,45:default --repeats 2

`default` keeps the map's sun azimuth; `road` puts the sun along the road, so that neither side camera
looks into it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time

from marshal_ai.app import MarshalApp
from marshal_ai.config import Settings
from marshal_ai.records import BuildSheet, Car, CarState
from marshal_ai.runtime import build_runtime

ROAD_AZIMUTH = 0.0          # Town05's plant road runs north-south; see plant_map.yaml yaw -90.7


def emit(obj):
    print(json.dumps(obj, default=str), flush=True)


def set_sun(world, altitude: float, azimuth: float | None):
    for _ in range(10):
        weather = world.get_weather()
        weather.sun_altitude_angle = altitude
        if azimuth is not None:
            weather.sun_azimuth_angle = azimuth
        world.set_weather(weather)
        time.sleep(0.5)
        now = world.get_weather()
        if abs(now.sun_altitude_angle - altitude) <= 0.5:
            return now.sun_altitude_angle, now.sun_azimuth_angle
    raise RuntimeError("sun did not apply")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suns", default="15:default,15:road,30:road,45:default")
    ap.add_argument("--repeats", type=int, default=2)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    rt = build_runtime(Settings(driving="carla", llm="gemini", store="memory", bus="memory"))
    app = MarshalApp(rt)
    vin = "VIN-LIGHT"
    rt.driving.despawn(vin)
    rt.driving.spawn(vin, at="inspection_gate", colour="0,80,200")
    rt.store.put(Car(vin=vin, state=CarState.INSPECTION_GATE, location="inspection_gate",
                     build_sheet=BuildSheet(model="sedan", colour="blue")))
    default_azimuth = rt.driving.world.get_weather().sun_azimuth_angle
    # Photographed exactly as in the plant scene: fixed gate cameras, streaming while the loop runs.
    rt.gate_camera.prewarm(fixed=True)
    await rt.driving.start()

    summary = {}
    for spec in args.suns.split(","):
        alt, az = spec.split(":")
        azimuth = None if az == "default" else (ROAD_AZIMUTH if az == "road" else float(az))
        applied = set_sun(rt.driving.world, float(alt), azimuth if azimuth is not None else default_azimuth)
        await asyncio.sleep(2.0)            # let the loop tick, so the cameras see the new light
        results = []
        for _ in range(args.repeats):
            r = await app.visual.inspect(vin)
            results.append(r.verdict.value)
            emit({"sun": spec, "applied": applied, "verdict": r.verdict.value,
                  "findings": [(f.zone, f.type, f.confidence) for f in r.findings],
                  "mismatches": r.build_sheet_mismatches})
        summary[spec] = {"pass": results.count("PASS"), "of": len(results)}
    await rt.driving.stop()
    rt.driving.despawn(vin)
    emit({"type": "gate_lighting.done", "summary": summary})
    return 0


if __name__ == "__main__":
    from marshal_ai.carla_connection import exit_without_teardown

    exit_without_teardown(asyncio.run(main()))
