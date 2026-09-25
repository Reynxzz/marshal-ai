import copy

import pytest

from marshal_ai.lab.generator import EXAMPLE_SPEC
from marshal_ai.lab import parse_criterion, validate_spec
from marshal_ai.plant import load_plant_map


def check(spec):
    return validate_spec(spec, load_plant_map())


def test_design_doc_example_is_valid():
    result = check(EXAMPLE_SPEC)
    assert result.ok, result.errors


@pytest.mark.parametrize("mutate, expected", [
    (lambda s: s["ego"].update(route="line_end->moon_base"), "unknown map point 'moon_base'"),
    (lambda s: s["ego"].update(speed_kmh=25), "ego.speed_kmh"),
    (lambda s: s.update(map_zone="rework"), "ego never passes rework"),
    (lambda s: s["actors"].pop(0), "behind:forklift but the scenario has no forklift"),
    (lambda s: s["actors"][1].pop("walk_speed_ms"), "needs walk_speed_ms"),
    (lambda s: s["actors"].append({"type": "dragon", "position": "finishing"}), "actors.2.type"),
    (lambda s: s["pass_criteria"].append("gap >= 1"), "unknown metric 'gap'"),
    (lambda s: s.update(pass_criteria=[]), "pass_criteria"),
    (lambda s: s.update(colour="red"), "colour"),
])
def test_invalid_specs_give_actionable_errors(mutate, expected):
    spec = copy.deepcopy(EXAMPLE_SPEC)
    mutate(spec)
    result = check(spec)
    assert not result.ok
    assert any(expected in e for e in result.errors), result.errors


def test_person_metric_needs_a_worker():
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["actors"] = [spec["actors"][0]]
    result = check(spec)
    assert any("need at least one worker" in e for e in result.errors)


def test_criteria_evaluate():
    assert parse_criterion("min_gap_to_person_m >= 1.5").holds({"min_gap_to_person_m": 1.6})
    assert not parse_criterion("min_gap_to_person_m >= 1.5").holds({"min_gap_to_person_m": 0.6})
    assert parse_criterion("no_contact").holds({"no_contact": 1})
