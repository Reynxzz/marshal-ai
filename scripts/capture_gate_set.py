#!/usr/bin/env python3
"""Photograph clean cars on the gate under several suns, for the gate's false-alarm dataset.

Qais's multi-car runs failed a clean car about one time in three ("panel gap" or "paint loss" along
the lower door edges, 0.85-0.95), and scripts/probe_gate_lighting.py showed it depends on the sun.
Dataset v1 has one clean car in one light, where the false-alarm rate was 0, so it cannot show this.
This captures the four gate views of a clean car in each colour under each sun, exactly as the plant
scene photographs them (fixed gate cameras streaming while the loop runs), and writes them with a
manifest. No Gemini here: the eval harness scores the frames (dataset v6-gate).

    python3 scripts/capture_gate_set.py --out /tmp/marshal-gate-set
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from marshal_ai.config import Settings
from marshal_ai.runtime import build_runtime

COLOURS = {"blue": "0,80,200", "white": "230,230,230", "grey": "120,120,120", "black": "20,20,20",
           "red": "200,20,20"}
SUNS = "15:default,15:road,30:road,45:default,45:road,60:default"
ROAD_AZIMUTH = 0.0          # the plant road runs north-south (as probe_gate_lighting.py)


def emit(obj):
    print(json.dumps(obj, default=str), flush=True)


def set_sun(world, altitude: float, azimuth: float):
    for _ in range(10):
        weather = world.get_weather()
        weather.sun_altitude_angle, weather.sun_azimuth_angle = altitude, azimuth
        world.set_weather(weather)
        time.sleep(0.5)
        now = world.get_weather()
        if abs(now.sun_altitude_angle - altitude) <= 0.5:
            return round(now.sun_altitude_angle, 1), round(now.sun_azimuth_angle, 1)
    raise RuntimeError("sun did not apply")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("/tmp/marshal-gate-set"))
    ap.add_argument("--colours", default=",".join(COLOURS))
    ap.add_argument("--suns", default=SUNS)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rt = build_runtime(Settings(driving="carla", llm="offline", store="memory", bus="memory",
                                artifacts="none", history="none"))
    world = rt.driving.world
    default_azimuth = world.get_weather().sun_azimuth_angle
    rt.gate_camera.prewarm(fixed=True)
    await rt.driving.start()
    manifest = []
    try:
        for colour in args.colours.split(","):
            vin = f"VIN-G-{colour.upper()}"
            rt.driving.despawn(vin)
            await asyncio.sleep(0.5)
            rt.driving.spawn(vin, at="inspection_gate", colour=COLOURS[colour])
            await asyncio.sleep(2.0)
            for spec in args.suns.split(","):
                alt, az = spec.split(":")
                azimuth = default_azimuth if az == "default" else ROAD_AZIMUTH if az == "road" else float(az)
                applied = set_sun(world, float(alt), azimuth)
                await asyncio.sleep(2.0)                     # the cameras see the new light
                case = f"{colour}-sun{alt}-{az}"
                views = {}
                for m in rt.gate_camera.capture(vin):
                    view = m.label.split()[0]
                    path = args.out / case / f"{view}.jpg"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(m.data)
                    views[view] = f"{case}/{view}.jpg"
                manifest.append({"case": case, "colour": colour, "sun": spec, "applied": applied, "views": views})
                emit({"type": "gate_set.case", "case": case, "applied": applied})
            rt.driving.despawn(vin)
    finally:
        await rt.driving.stop()
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    emit({"type": "gate_set.done", "cases": len(manifest), "out": str(args.out)})
    return 0


if __name__ == "__main__":
    from marshal_ai.carla_connection import exit_without_teardown

    exit_without_teardown(asyncio.run(main()))
