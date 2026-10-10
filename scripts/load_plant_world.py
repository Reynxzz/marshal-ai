#!/usr/bin/env python3
"""Build the plant's own world from OpenDRIVE on the CARLA server, and check it can carry the demo.

    python3 scripts/plant_xodr.py --out /tmp/marshal_plant.xodr
    python3 scripts/load_plant_world.py --xodr /tmp/marshal_plant.xodr --check --out /tmp/marshal-plant-world

Builds the world (client.generate_opendrive_world, walls along the road edges), resolves each plant
point from (road, lane, s) to a CARLA transform, and with --check: drives a car with BehaviorAgent
from the line end round to yard slot Y5, photographs the plant, and keeps the server ticking for a
minute to see it stays up (Town05_Opt with layers unloaded did not). --restore loads Town05 after.
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import time
from pathlib import Path

import carla


def emit(obj):
    print(json.dumps(obj), flush=True)


def build(client, xodr: str):
    params = carla.OpendriveGenerationParameters(
        vertex_distance=2.0, max_road_length=500.0, wall_height=1.6, additional_width=0.6,
        smooth_junctions=True, enable_mesh_visibility=True, enable_pedestrian_navigation=True)
    world = client.generate_opendrive_world(xodr, params)
    world.wait_for_tick(30.0)
    return world


def resolve(m: carla.Map, points: dict) -> dict:
    out = {}
    for name, (road, lane, s) in points.items():
        wp = m.get_waypoint_xodr(road, lane, s)
        if wp is None:
            out[name] = None
            continue
        t = wp.transform
        out[name] = {"x": round(t.location.x, 2), "y": round(t.location.y, 2), "z": round(t.location.z + 0.5, 2),
                     "yaw": round(t.rotation.yaw, 1), "lane_type": str(wp.lane_type)}
    return out


def photo(world, loc, rot, path: Path, size=(1280, 720), fov=85):
    bp = world.get_blueprint_library().find("sensor.camera.rgb")
    bp.set_attribute("image_size_x", str(size[0]))
    bp.set_attribute("image_size_y", str(size[1]))
    bp.set_attribute("fov", str(fov))
    cam = world.spawn_actor(bp, carla.Transform(loc, rot))
    q: queue.Queue = queue.Queue()
    cam.listen(q.put)
    img = None
    for _ in range(60):
        if world.get_settings().synchronous_mode:
            world.tick()
        try:
            img = q.get(timeout=1.0)
        except queue.Empty:
            continue
        if img.frame > 0:
            break
    cam.stop()
    cam.destroy()
    if img is not None:
        img.save_to_disk(str(path))
    return img is not None


def drive(world, m, pts: dict, out: Path, timeout_s: float = 240.0) -> dict:
    from agents.navigation.behavior_agent import BehaviorAgent

    start, goal = pts["line_end"], pts["yard.Y5"]
    bp = world.get_blueprint_library().find("vehicle.lincoln.mkz_2020")
    bp.set_attribute("color", "0,80,200")
    car = world.spawn_actor(bp, carla.Transform(carla.Location(start["x"], start["y"], start["z"]),
                                                carla.Rotation(yaw=start["yaw"])))
    original = world.get_settings()                    # a copy: restored at the end
    settings = world.get_settings()
    settings.synchronous_mode, settings.fixed_delta_seconds = True, 0.05
    world.apply_settings(settings)
    try:
        world.tick()
        agent = BehaviorAgent(car, behavior="cautious")
        agent.set_destination(carla.Location(goal["x"], goal["y"], goal["z"]))
        ticks, reached, travelled, last = 0, False, 0.0, car.get_location()
        while ticks < timeout_s / 0.05:
            car.apply_control(agent.run_step())
            world.tick()
            ticks += 1
            here = car.get_location()
            travelled += here.distance(last)
            last = here
            if agent.done():
                reached = True
                break
        end = car.get_location()
        miss = math.dist((end.x, end.y), (goal["x"], goal["y"]))
        # the car on the bend and in the yard, from above and behind
        photo(world, end + carla.Location(x=-14, z=9), carla.Rotation(pitch=-28, yaw=0), out / "car_in_yard.png")
        return {"reached": reached, "sim_s": round(ticks * 0.05, 1), "travelled_m": round(travelled, 1),
                "end_off_goal_m": round(miss, 2)}
    finally:
        car.destroy()
        world.apply_settings(original)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xodr", type=Path, default=Path("/tmp/marshal_plant.xodr"))
    ap.add_argument("--out", type=Path, default=Path("/tmp/marshal-plant-world"))
    ap.add_argument("--check", action="store_true", help="drive a car round, photograph, watch stability")
    ap.add_argument("--restore", action="store_true", help="load Town05 again at the end")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    client = carla.Client(args.host, args.port)
    client.set_timeout(180.0)
    world = build(client, args.xodr.read_text())
    m = world.get_map()
    points = json.loads(args.xodr.with_suffix(".points.json").read_text())
    pts = resolve(m, points)
    (args.out / "points.json").write_text(json.dumps(pts, indent=1))
    emit({"type": "plant_world.built", "map": m.name, "points": sum(v is not None for v in pts.values()),
          "missing": [k for k, v in pts.items() if v is None]})
    if args.check:
        weather = world.get_weather()
        weather.sun_altitude_angle = 45.0
        world.set_weather(weather)
        g = pts["inspection_gate"]
        photo(world, carla.Location(g["x"] - 30, g["y"] + 12, 7), carla.Rotation(pitch=-12, yaw=-15), args.out / "along_south.png")
        photo(world, carla.Location(82, -52, 135), carla.Rotation(pitch=-90), args.out / "top.png", (1000, 1000), 90)
        emit({"type": "plant_world.drive", **drive(world, m, pts, args.out)})
        alive = 0
        for _ in range(6):
            time.sleep(10)
            world.wait_for_tick(20.0)
            alive += 10
        emit({"type": "plant_world.stable", "seconds": alive})
    if args.restore:
        client.load_world("Town05")
        emit({"type": "plant_world.restored", "map": "Town05"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
