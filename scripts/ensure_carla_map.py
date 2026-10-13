#!/usr/bin/env python3
"""Wait for CARLA and optionally load the requested map."""

from __future__ import annotations

import argparse
import time

import carla


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--map", default="Town05")
    ap.add_argument("--wait-seconds", type=float, default=600.0)
    args = ap.parse_args()

    deadline = time.monotonic() + args.wait_seconds
    client = carla.Client(args.host, args.port)
    last_error = None
    while time.monotonic() < deadline:
        try:
            client.set_timeout(min(10.0, max(1.0, deadline - time.monotonic())))
            world = client.get_world()
            break
        except RuntimeError as exc:
            last_error = exc
            time.sleep(3.0)
    else:
        raise SystemExit(f"CARLA did not become ready: {last_error}")

    current = world.get_map().name.rsplit("/", 1)[-1]
    if args.map == "MarshalPlant":
        # The plant's own world is built from OpenDRIVE every time: a generated world is not one CARLA
        # can load by name, and a server restart loses it.
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from load_plant_world import build
        from plant_xodr import xodr
        client.set_timeout(180.0)
        world = build(client, xodr())
    elif current != args.map:
        client.set_timeout(180.0)
        world = client.load_world(args.map)
        world.wait_for_tick(60.0)
    print(f"CARLA ready: {client.get_server_version()} {world.get_map().name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
