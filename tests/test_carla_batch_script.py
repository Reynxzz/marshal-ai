from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from demo_carla_plant import emit_bus_events, load_manifest  # noqa: E402

from marshal_ai.batch.manifest import build_manifest


def test_script_reads_manifest_and_rejects_unsafe_entries(tmp_path) -> None:
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(build_manifest("BAT-TEST", 3, seed=5)))
    manifest = load_manifest(path)
    assert len(manifest["cars"]) == 3
    assert len({car["vin"] for car in manifest["cars"]}) == 3

    path.write_text(json.dumps({"cars": [{"vin": "VIN-1"}] * 7}))
    with pytest.raises(ValueError, match="1–6"):
        load_manifest(path)


def test_batch_event_jsonl_carries_batch_id() -> None:
    class Event:
        seq = 1
        at = "12:00:00"
        flow = 3
        source = "Visual inspector"
        title = "Gate passed"
        detail = "VIN-1"
        level = "good"

        def as_dict(self):
            return {key: getattr(self, key) for key in
                    ("seq", "at", "flow", "source", "title", "detail", "level")}

    class Log:
        def since(self, seq):
            return [Event()] if seq < 1 else []

    emitted = []
    import demo_carla_plant
    original = demo_carla_plant.emit
    try:
        demo_carla_plant.emit = emitted.append
        assert emit_bus_events(Log(), "BAT-1", 0) == 1
    finally:
        demo_carla_plant.emit = original
    assert emitted[0]["type"] == "batch.event" and emitted[0]["batch_id"] == "BAT-1"
