"""Flow 6's retrieval library: validated specs shown as examples for a new request (ChatScene)."""

import pytest

from marshal_ai.lab.generator import ScenarioGenerator
from marshal_ai.lab.judge import judge
from marshal_ai.lab.library import SEEDS, Example, library_examples, retrieve
from marshal_ai.lab.reference import unfair_reasons
from marshal_ai.lab.runner import SimRunner
from marshal_ai.lab.validate import validate_spec
from marshal_ai.plant import load_plant_map
from marshal_ai.records import Scenario, ScenarioStatus


@pytest.mark.parametrize("example", SEEDS, ids=lambda e: e.id)
def test_every_seed_example_is_a_valid_passing_spec(example):
    """An example teaches its form to every request it is shown with, so each must be right."""
    m = load_plant_map()
    check = validate_spec(example.spec, m)
    assert check.ok, check.errors
    assert not unfair_reasons(check.spec, m)
    out = SimRunner(m).run(check.spec)
    assert not ScenarioGenerator._dry_run_errors(check.spec, out)       # the generator's own dry run
    assert judge(check.spec, out.metrics).passed


@pytest.mark.parametrize("request_text, first", [
    ("The vehicle in front of the car stops hard in fog.", "SC-0160"),
    ("A pedestrian walks across the test track in the evening.", "SC-0180"),
    ("At night a forklift drives across the finishing corner.", "SC-0170"),
    ("A worker steps out from behind a parked forklift at dusk.", "SC-0142"),
])
def test_the_closest_example_comes_first(request_text, first):
    assert retrieve(request_text, "supervisor request", SEEDS)[0].id == first


def test_a_case_from_the_desk_is_always_shown_an_incident_example():
    shown = retrieve("A forklift is crossing the lane at the finishing corner.", "assistance case AC-1", SEEDS)
    assert len(shown) == 2 and any(e.from_incident for e in shown)


def test_reviewed_scenarios_join_the_library_but_regression_variants_do_not(app):
    store = app.rt.store
    spec = dict(SEEDS[3].spec, id="SC-0200")
    store.put(Scenario(id="SC-0200", spec=spec, source="supervisor request", status=ScenarioStatus.IN_LIBRARY,
                       notes="A tow tractor crosses the corner | approved"))
    store.put(Scenario(id="SC-0200-v3", spec=dict(spec, id="SC-0200-v3"), source="adversarial search",
                       status=ScenarioStatus.IN_LIBRARY, tags=["regression"]))
    store.put(Scenario(id="SC-0201", spec=dict(spec, id="SC-0201"), source="supervisor request",
                       status=ScenarioStatus.DRY_RUN_OK, notes="not reviewed yet"))
    ids = {e.id: e for e in library_examples(store)}
    assert "SC-0200" in ids and ids["SC-0200"].request == "A tow tractor crosses the corner"
    assert "SC-0200-v3" not in ids and "SC-0201" not in ids
    assert retrieve("a tow tractor crosses", "supervisor request", list(ids.values()))[0].id == "SC-0200"


def test_the_generator_shows_the_retrieved_examples(app):
    calls = []
    original = app.rt.llm.handlers["scenario.spec"]
    app.rt.llm.on("scenario.spec", lambda call: calls.append(call.prompt) or original(call))
    app.generator.generate("The vehicle in front of the car stops hard in fog.")
    assert app.generator.last_examples[0] == "SC-0160"
    assert "The car in front brakes suddenly" in calls[0]
    app.generator.retrieval = False                 # the baseline: the design example only
    app.generator.generate("The vehicle in front of the car stops hard in fog.")
    assert app.generator.last_examples == ["SC-0142"]
