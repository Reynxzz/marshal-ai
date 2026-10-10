"""The replay labels name where the car is: a bay or slot only when it stops there."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import film_labels as f  # noqa: E402

NAMED = f.plant_points()


def _stops_at(*path):
    return list(path) + [path[-1]] * 10            # the car stands still at the end


def test_stopping_at_a_bay_names_the_bay():
    assert f.zone_for(_stops_at((207, -80, 0), (207, -95, 0), (213.3, -88.1, 0)), NAMED) == "Rework bay · electrical"


def test_a_shot_that_ends_beside_a_slot_while_driving_is_not_parked_there():
    assert "slot" not in f.zone_for([(206, -120, 0), (206.5, -130, 0), (206.6, -136.3, 0)], NAMED)


def test_driving_past_the_bays_is_not_being_in_one():
    assert "Rework" not in f.zone_for([(207, -80, 0), (207, -95, 0), (207, -110, 0)], NAMED)


def test_parking_in_a_slot_names_that_slot():
    assert f.zone_for(_stops_at((206, -120, 0), (203, -140, 0), (202.8, -145.6, 0)), NAMED) == "Yard · slot Y2"


def test_the_gate_and_run_titles():
    assert f.zone_for([(207, -28, 0), (207, -34, 0), (207, -40, 0)], NAMED) == "Inspection gate"
    assert f.run_title("day4-20261010-033908") == "Flows 4-5 · Lamp check and rework"


def test_without_pillow_the_frame_is_left_as_it_is(monkeypatch):
    monkeypatch.setattr(f, "Image", None)
    raw = bytes(4 * 8 * 6)
    assert f.Labeller(8, 6).draw(raw, "t", "Test track", "VIN-1") is raw
