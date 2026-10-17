#!/usr/bin/env python3
"""Which vehicle blueprints can show a dead left brake light to a camera.

CARLA 0.9.16's light API has one Brake bit for both brake lamps, so it cannot switch off one
side. A fault on one side can still be shown if an asset's turn signal sits in the red tail lamp:
then RightBlinker alone lights only the right red lamp - which is what a car with a dead left brake
light looks like when it brakes. CARLA draws a blinker steadily lit, not flashing.

For every four-wheeled blueprint this parks the car on the test track at night and photographs its
rear, with the same pose and lens as the car's own rear evidence camera, under each light state.
For each half of the image (the car's left and right) it measures how much brighter, and how red,
the frame got against lights-off. Contact sheets go to --output.

    python3 scripts/probe_lamps.py --output /tmp/marshal-lamps
"""

from __future__ import annotations

import argparse
import json
import queue
from pathlib import Path

import carla
import numpy as np
from PIL import Image

STATES = ["NONE", "Brake", "LeftBlinker", "RightBlinker"]
REAR = carla.Transform(carla.Location(x=-7.0, z=1.6), carla.Rotation(pitch=-5.0))   # ONBOARD_VIEWS["rear"]
TEST_TRACK = carla.Transform(carla.Location(x=206.74, y=-53.93, z=0.5), carla.Rotation(yaw=-90.7))


def emit(obj) -> None:
    print(json.dumps(obj, sort_keys=True), flush=True)


def frame(world, images: queue.Queue, ticks: int = 6) -> np.ndarray:
    """The newest frame after a few ticks, so a light change has been rendered."""
    for _ in range(ticks):
        world.tick()
    latest = None
    while True:
        try:
            latest = images.get(timeout=2.0 if latest is None else 0.05)
        except queue.Empty:
            break
    raw = np.frombuffer(latest.raw_data, dtype=np.uint8).reshape(latest.height, latest.width, 4)
    return raw[:, :, :3][:, :, ::-1].astype(np.int16)             # BGRA -> RGB


def lit(base: np.ndarray, img: np.ndarray) -> dict:
    """Light added against lights-off, per half: total, and the part of it that is red, not amber."""
    gain = np.clip(img - base, 0, None)
    w = gain.shape[1] // 2
    out = {}
    for side, part in (("left", gain[:, :w]), ("right", gain[:, w:])):
        r, g = part[:, :, 0].astype(np.int64), part[:, :, 1].astype(np.int64)
        out[side] = int(r.sum() + g.sum() + part[:, :, 2].sum()) // 1000
        # red lamp light raises R far more than G; amber raises both
        out[side + "_red"] = int(np.clip(r - g, 0, None).sum()) // 1000
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--output", default="/tmp/marshal-lamps")
    ap.add_argument("--only", default="", help="comma-separated blueprint ids")
    args = ap.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    client = carla.Client(args.host, args.port)
    client.set_timeout(30.0)
    world = client.get_world()
    original = world.get_settings()
    settings = world.get_settings()
    settings.synchronous_mode, settings.fixed_delta_seconds = True, 0.05
    world.apply_settings(settings)
    weather = world.get_weather()
    weather.sun_altitude_angle = -10.0
    world.set_weather(weather)

    lib = world.get_blueprint_library()
    blueprints = [bp for bp in lib.filter("vehicle.*")
                  if bp.has_attribute("number_of_wheels") and bp.get_attribute("number_of_wheels").as_int() == 4]
    if args.only:
        wanted = set(args.only.split(","))
        blueprints = [bp for bp in blueprints if bp.id in wanted]
    cam_bp = lib.find("sensor.camera.rgb")
    cam_bp.set_attribute("image_size_x", "640")
    cam_bp.set_attribute("image_size_y", "360")
    cam_bp.set_attribute("fov", "38")
    if cam_bp.has_attribute("role_name"):
        cam_bp.set_attribute("role_name", "marshal-sensor")

    results = []
    try:
        for bp in sorted(blueprints, key=lambda b: b.id):
            if bp.has_attribute("role_name"):
                bp.set_attribute("role_name", "marshal-lamp-probe")
            car = world.try_spawn_actor(bp, TEST_TRACK)
            if car is None:
                emit({"blueprint": bp.id, "error": "could not spawn"})
                continue
            cam = None
            try:
                car.apply_control(carla.VehicleControl(brake=1.0, hand_brake=True))
                cam = world.spawn_actor(cam_bp, REAR, attach_to=car)
                images: queue.Queue = queue.Queue()
                cam.listen(images.put)
                shots = {}
                for state in STATES:
                    car.set_light_state(carla.VehicleLightState(getattr(carla.VehicleLightState, state)))
                    shots[state] = frame(world, images, ticks=10 if state == "NONE" else 6)
                gains = {s: lit(shots["NONE"], shots[s]) for s in STATES[1:]}
                row = {"blueprint": bp.id, **{s: gains[s] for s in gains}}
                results.append(row)
                emit(row)
                sheet = np.concatenate([shots[s] for s in STATES], axis=1).clip(0, 255).astype(np.uint8)
                Image.fromarray(sheet).save(out / f"{bp.id}.jpg", quality=85)
            finally:
                if cam is not None:
                    cam.stop()
                    cam.destroy()
                car.destroy()
                world.tick()
    finally:
        world.apply_settings(original)

    (out / "lamps.json").write_text(json.dumps(results, indent=2, sort_keys=True))
    emit({"type": "lamps.done", "blueprints": len(results), "output": str(out)})
    return 0


if __name__ == "__main__":
    import os
    import sys

    code = main()
    sys.stdout.flush()
    os._exit(code)          # skip the CARLA client's teardown; see exit_without_teardown
