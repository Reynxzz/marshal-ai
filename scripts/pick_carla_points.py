"""Print the CARLA map's spawn points so plant map points can be pinned to real transforms.

Run on the GPU VM with the CARLA server up:

    python scripts/pick_carla_points.py --map Town05

Copy the chosen transforms into the `carla:` field of each point in
src/marshal_ai/plant/plant_map.yaml, e.g. carla: {x: 10.2, y: -45.1, z: 0.5, yaw: 90}.
"""

from __future__ import annotations

import argparse

import carla


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--map", default=None, help="load this map first, e.g. Town05")
    args = parser.parse_args()

    client = carla.Client(args.host, args.port)
    client.set_timeout(20.0)
    world = client.load_world(args.map) if args.map else client.get_world()
    print(f"map: {world.get_map().name}")
    for i, t in enumerate(world.get_map().get_spawn_points()):
        loc, rot = t.location, t.rotation
        print(f"{i:3d}  carla: {{x: {loc.x:.1f}, y: {loc.y:.1f}, z: {loc.z:.1f}, yaw: {rot.yaw:.0f}}}")


if __name__ == "__main__":
    main()
