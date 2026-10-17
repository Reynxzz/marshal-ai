"""Deterministic sample cars and CARLA labels for a batch."""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

COLORS = {
    "blue": "0,80,200",
    "white": "255,255,255",
    "grey": "120,120,120",
    "black": "20,20,20",
}
WRONG_COLOR = "200,20,20"


def build_manifest(batch_id: str, cars: int, seed: int | None = None) -> dict:
    if not 1 <= cars <= 6:
        raise ValueError("cars must be between 1 and 6")
    short_id = re.sub(r"[^A-Z0-9]", "", batch_id.upper())[-10:] or "BATCH"
    if seed is None:
        seed = int.from_bytes(hashlib.blake2b(batch_id.encode(), digest_size=4).digest(), "big")
    rng = random.Random(seed)
    if cars >= 3:
        wrong, dead = rng.sample(range(cars - 1), 2)
        incident = cars - 1
    elif cars == 2:
        wrong, dead, incident = rng.randrange(cars), None, None
    else:
        wrong = dead = incident = None
    trucks = [
        {"truck_id": f"T-{i + 1}", "eta_min": 20 + 20 * i}
        for i in range(min(cars, 2))
    ] or [{"truck_id": "T-1", "eta_min": 20}]

    rows = []
    for index in range(cars):
        colour = rng.choice(tuple(COLORS))
        kind = "wrong_colour" if index == wrong else "dead_lamp" if index == dead else "incident" if index == incident else "clean"
        fault_step = ("headlights" if index % 2 == 0 else "brake_lights") if kind == "dead_lamp" else None
        rows.append({
            "vin": f"VIN-{short_id}-{index + 1}",
            "build_sheet": {"model": "sedan", "colour": colour, "options": []},
            "spawn_colour": WRONG_COLOR if kind == "wrong_colour" else COLORS[colour],
            "truck_id": trucks[index % len(trucks)]["truck_id"],
            "expected": {"kind": kind, "fault_step": fault_step},
        })

    incident_row = rows[incident] if incident is not None else None
    return {
        "batch_id": batch_id,
        "seed": seed,
        "engine": "carla",
        "incident": {
            "enabled": incident_row is not None,
            "vin": incident_row["vin"] if incident_row else None,
            "kind": "box_worker" if incident_row else None,
            "situation": (
                "A cardboard box with a worker beside it blocks the lane on the way to the yard."
                if incident_row else None
            ),
        },
        "trucks": trucks,
        "cars": rows,
    }


def write_manifest(manifest: dict, path: Path) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def manifest_labels(manifest: dict) -> dict[str, dict]:
    return {car["vin"]: dict(car["expected"]) for car in manifest["cars"]}


def is_finished(state: str) -> bool:
    return state in {"yard", "shipped", "rework"}
