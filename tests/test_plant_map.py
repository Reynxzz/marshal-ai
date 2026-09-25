import pytest

from marshal_ai.plant import load_plant_map


def test_every_zone_has_points():
    m = load_plant_map()
    for zone in m.zones:
        assert m.points_in_zone(zone), zone


def test_route_line_end_to_yard_slot_passes_gate_and_track():
    m = load_plant_map()
    path = m.route("line_end", "yard.Y7")
    assert path[0] == "line_end" and path[-1] == "yard.Y7"
    assert "inspection_gate" in path and "test_track" in path


def test_every_point_reachable_from_line_end():
    m = load_plant_map()
    for p in m.points:
        m.route("line_end", p)


def test_unknown_point_raises():
    with pytest.raises(KeyError):
        load_plant_map().route("line_end", "nowhere")


def test_rework_exits_through_the_gate():
    m = load_plant_map()
    path = m.route("rework.mechanical", "yard.Y1")
    assert "inspection_gate" in path and "test_track" in path
