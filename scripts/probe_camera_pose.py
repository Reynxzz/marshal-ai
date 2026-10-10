#!/usr/bin/env python3
"""Probe whether a camera at a given pose crashes the CARLA renderer.

One camera at a time: spawn it, wait for frames, destroy it, report. Stops at the first pose the
server does not survive, since every later probe would only time out against a dead server.

    python3 scripts/probe_camera_pose.py 221,-27,7,163            # x,y,z,yaw
    python3 scripts/probe_camera_pose.py 221,-27,7,163 221,-27,7,0 --frames 10
    python3 scripts/probe_camera_pose.py --grid 200:240:10,-40:-10:10,7,163

Each line printed is JSON: {"pose": [...], "ok": true, "frames": 5, "secs": 1.2} or "ok": false
with the error. Run with nothing else connected to the server; the camera is tagged marshal-sensor
so a later backend sweeps it if this script dies mid-probe.
"""

from __future__ import annotations

import argparse
import itertools
import json
import threading
import time

import carla

SENSOR_ROLE = "marshal-sensor"


def poses_from(args) -> list[tuple[float, float, float, float]]:
    out = [tuple(float(v) for v in p.split(",")) for p in args.pose]
    if args.grid:
        axes = []
        for part in args.grid.split(","):
            if ":" in part:
                lo, hi, step = (float(v) for v in part.split(":"))
                n = int(round((hi - lo) / step)) + 1
                axes.append([lo + i * step for i in range(n)])
            else:
                axes.append([float(part)])
        out += list(itertools.product(*axes))
    return out


def probe(world, pose, frames_wanted: int, timeout_s: float, fov: float, pitch: float) -> dict:
    x, y, z, yaw = pose
    bp = world.get_blueprint_library().find("sensor.camera.rgb")
    bp.set_attribute("image_size_x", "1280")
    bp.set_attribute("image_size_y", "720")
    bp.set_attribute("fov", str(fov))
    bp.set_attribute("role_name", SENSOR_ROLE)
    got = []
    done = threading.Event()

    def receive(image):
        got.append(image.frame)
        if len(got) >= frames_wanted:
            done.set()

    t0 = time.monotonic()
    cam = world.spawn_actor(bp, carla.Transform(carla.Location(x=x, y=y, z=z),
                                                carla.Rotation(yaw=yaw, pitch=pitch)))
    try:
        cam.listen(receive)
        done.wait(timeout_s)
    finally:
        cam.stop()
        cam.destroy()
    return {"pose": [x, y, z, yaw], "ok": len(got) >= frames_wanted, "frames": len(got),
            "secs": round(time.monotonic() - t0, 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pose", nargs="*", help="x,y,z,yaw")
    ap.add_argument("--grid", help="x_lo:x_hi:step,y_lo:y_hi:step,z,yaw (any axis may be a range)")
    ap.add_argument("--frames", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--fov", type=float, default=90.0)
    ap.add_argument("--pitch", type=float, default=-15.0)
    ap.add_argument("--sun-azimuth", type=float, help="move the sun before probing")
    args = ap.parse_args()

    client = carla.Client("localhost", 2000)
    client.set_timeout(20.0)
    world = client.get_world()
    if world.get_settings().synchronous_mode:
        raise SystemExit("world is in synchronous mode; restart CARLA first")
    if args.sun_azimuth is not None:
        weather = world.get_weather()
        weather.sun_azimuth_angle = args.sun_azimuth
        world.set_weather(weather)
        time.sleep(1.0)

    for pose in poses_from(args):
        try:
            result = probe(world, pose, args.frames, args.timeout, args.fov, args.pitch)
        except RuntimeError as exc:                # the server stopped answering
            print(json.dumps({"pose": list(pose), "ok": False, "error": str(exc)[:120]}), flush=True)
            return 1
        print(json.dumps(result), flush=True)
        if not result["ok"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
