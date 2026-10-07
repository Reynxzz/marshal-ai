#!/usr/bin/env python3
"""Check CARLA connectivity and version without mutating the running world."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from marshal_ai.carla_connection import CarlaConnectionError, connect_carla
from marshal_ai.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=settings.carla_host)
    parser.add_argument("--port", type=int, default=settings.carla_port)
    parser.add_argument("--timeout", type=float, default=settings.carla_timeout_s)
    parser.add_argument("--expect-version", default=settings.carla_version)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        _, info = connect_carla(
            args.host,
            args.port,
            timeout_s=args.timeout,
            expected_version=args.expect_version,
        )
    except CarlaConnectionError as exc:
        parser.exit(1, f"CARLA connection failed: {exc}\n")

    data = asdict(info)
    if args.json:
        print(json.dumps(data, sort_keys=True))
    else:
        print(f"Connected to CARLA at {info.host}:{info.port}")
        print(f"Client/server: {info.client_version} / {info.server_version}")
        print(f"Map: {info.map_name}; actors: {info.actor_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
