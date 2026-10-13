"""What the agents decided during a recorded run, for the filmed replay to show as it happens.

A replay shows cars moving, not why. The demos write each decision here - a release under the yard
rules, a gate verdict, a slot chosen by truck, Gemini's re-plan, a supervisor's approval - stamped
with the simulator's clock, and scripts/replay_carla.py draws them as a feed in the corner of the
film at the moment they were made.

The recorder's own file is written by the CARLA service, under /var/lib/carla; these sit beside it
in the user's cache, keyed by the recording's name. The run script exports that name as
MARSHAL_RECORDING (scripts/carla_recording.sh), so every phase process of a run writes to the same
file. Without it nothing is written, and the film is made without the feed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

NOTES_DIR = Path(os.environ.get("MARSHAL_NOTES_DIR", Path.home() / ".cache/marshal/recordings"))


def start_file(name: str) -> Path:
    return NOTES_DIR / f"{name}.start.json"


def notes_file(name: str) -> Path:
    return NOTES_DIR / f"{name}.notes.jsonl"


def mark_start(name: str, elapsed_s: float) -> None:
    """The simulator's clock when the recorder started: recording time 0."""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    start_file(name).write_text(json.dumps({"elapsed_s": elapsed_s}))


def note(world, text: str, who: str = "rules") -> None:
    """Record a decision at the simulator's current time. `who`: rules, gemini, supervisor, plant."""
    name = os.environ.get("MARSHAL_RECORDING")
    if not name:
        return
    try:
        elapsed = world.get_snapshot().timestamp.elapsed_seconds
    except RuntimeError:
        return
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    with notes_file(name).open("a") as f:
        f.write(json.dumps({"elapsed_s": elapsed, "who": who, "text": text}) + "\n")


def load(name: str) -> list[dict]:
    """The run's notes, each with `t`: seconds into the recording. Empty if there are none."""
    try:
        start = json.loads(start_file(name).read_text())["elapsed_s"]
        lines = notes_file(name).read_text().splitlines()
    except (OSError, ValueError, KeyError):
        return []
    out = []
    for line in lines:
        try:
            n = json.loads(line)
        except ValueError:
            continue
        out.append({"t": n["elapsed_s"] - start, "who": n.get("who", "rules"), "text": n["text"]})
    return sorted(out, key=lambda n: n["t"])
