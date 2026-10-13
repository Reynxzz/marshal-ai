"""Start or stop CARLA's server-side recorder, so a run can be replayed and filmed afterwards.

The recorder lives in the server, not in this process, so it keeps recording across the
one-process-per-phase demos until `stop` is called. The file is written by the CARLA service
itself, which may only write under /var/lib/carla.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

DEFAULT_DIR = "/var/lib/carla/recordings"


def recording_path(name: str, directory: str = DEFAULT_DIR) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise SystemExit(f"recording name has unsupported characters: {name}")
    return f"{directory}/{name}.log"


def duration_s(info: str) -> float | None:
    """Duration from show_recorder_file_info, which ends with 'Duration: N seconds'."""
    match = re.search(r"Duration:\s*([0-9.]+)\s*seconds", info)
    return float(match.group(1)) if match else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("start", "stop", "info"))
    ap.add_argument("--name", help="recording name (start, info)")
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    args = ap.parse_args(argv)

    import carla

    client = carla.Client(args.host, args.port)
    client.set_timeout(20.0)
    if args.action == "start":
        if not args.name:
            raise SystemExit("start needs --name")
        path = recording_path(args.name, args.dir)
        # additional_data=True also keeps lights and kinematics, so headlights show in the replay
        client.start_recorder(path, True)
        # recording time 0 on the simulator's clock, so decisions noted during the run can be
        # placed in the film (scripts/film_notes.py)
        from film_notes import mark_start
        mark_start(args.name, client.get_world().get_snapshot().timestamp.elapsed_seconds)
        print(json.dumps({"type": "recorder.started", "file": path}))
    elif args.action == "stop":
        client.stop_recorder()
        print(json.dumps({"type": "recorder.stopped"}))
    else:
        if not args.name:
            raise SystemExit("info needs --name")
        path = recording_path(args.name, args.dir)
        info = client.show_recorder_file_info(path, False)
        print(json.dumps({"type": "recorder.info", "file": path, "duration_s": duration_s(info)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
