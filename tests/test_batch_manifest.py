from __future__ import annotations

import json

import pytest

from marshal_ai.batch.manifest import build_manifest, is_finished, manifest_labels, write_manifest
from marshal_ai.batch.stages import stage_for


def test_manifest_is_repeatable_and_labels_each_generated_car(tmp_path) -> None:
    one = build_manifest("BAT-X", 3, seed=7)
    assert one == build_manifest("BAT-X", 3, seed=7)
    assert len(one["cars"]) == 3
    assert len({car["vin"] for car in one["cars"]}) == 3
    assert {car["expected"]["kind"] for car in one["cars"]} == {
        "wrong_colour", "dead_lamp", "incident"
    }
    assert one["incident"]["enabled"]
    assert all(car["spawn_colour"].count(",") == 2 for car in one["cars"])
    assert len(manifest_labels(one)) == 3
    path = tmp_path / "manifest.json"
    write_manifest(one, path)
    assert json.loads(path.read_text()) == one


@pytest.mark.parametrize("size", [1, 2, 3, 6])
def test_manifest_size_and_incident_rules(size) -> None:
    manifest = build_manifest("BAT-SIZE", size, seed=3)
    assert len(manifest["cars"]) == size
    assert manifest["incident"]["enabled"] is (size >= 3)
    assert sum(car["expected"]["kind"] == "wrong_colour" for car in manifest["cars"]) == int(size >= 2)
    assert sum(car["expected"]["kind"] == "dead_lamp" for car in manifest["cars"]) == int(size >= 3)


def test_manifest_bounds_and_stage_helpers() -> None:
    with pytest.raises(ValueError):
        build_manifest("BAT-BAD", 7)
    assert is_finished("yard") and is_finished("shipped")
    assert not is_finished("test_track")
    assert stage_for("test_track") == 4
    assert stage_for("line_end", has_open_case=True) == 2
