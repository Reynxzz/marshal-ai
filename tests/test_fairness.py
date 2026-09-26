"""Tests must be fair: a careful reference driver has to be able to pass them."""

import copy

from marshal_ai.app import RELEASES
from marshal_ai.lab.adversarial import apply_variation
from marshal_ai.lab.generator import EXAMPLE_SPEC
from marshal_ai.lab.judge import judge
from marshal_ai.lab.reference import unfair_reasons
from marshal_ai.lab.sim import simulate
from marshal_ai.lab.spec import ScenarioSpec
from marshal_ai.llm import FakeLLM
from marshal_ai.plant import load_plant_map
from marshal_ai.records import Scenario

DOC_REQUEST = ("A worker steps out from behind a parked forklift at dusk while a car turns "
               "into the finishing lane.")


def variant(**changes):
    return apply_variation(ScenarioSpec.model_validate(EXAMPLE_SPEC), changes, "SC-0142-v1")


def test_step_out_too_close_to_stop_for_is_unavoidable_but_night_is_fair():
    m = load_plant_map()
    assert unfair_reasons(variant(trigger_distance_m=4.0), m)
    assert not unfair_reasons(variant(light="night"), m)
    assert not unfair_reasons(variant(ego_speed_kmh=10.0), m)


def test_generator_sends_impossible_criteria_back_to_the_model(app):
    good = {k: v for k, v in EXAMPLE_SPEC.items() if k not in ("id", "source")}
    impossible = copy.deepcopy(good)
    impossible["pass_criteria"] = ["no_contact", "max_speed_kmh <= 0.1"]
    answers = iter([impossible, good])
    app.rt.llm = FakeLLM({"scenario.spec": lambda call: next(answers)})
    result = app.generator.generate("anything")
    assert result.ok and result.attempts == 2
    assert "not achievable" in app.rt.llm.calls[1].prompt


def test_search_skips_unavoidable_variations_and_never_adds_them_to_the_suite(app):
    gen = app.generator.generate(DOC_REQUEST)
    app.generator.review(gen.scenario.id, True, "eng-kim")
    batches = iter([[{"trigger_distance_m": 4.0}, {"trigger_distance_m": 5.0}], [{"light": "night"}]])
    app.rt.llm = FakeLLM({
        "adversarial.propose": lambda call: {"reasoning": "closer", "variations": next(batches)},
        "adversarial.report": lambda call: {"summary": "s", "likely_cause": "c", "suggested_fix": "f"},
    })
    app.adversarial.batch = 2
    result = app.adversarial.search(gen.scenario.id)
    skipped = [u["changes"] for u in result.unavoidable]
    assert {"trigger_distance_m": 4.0} in skipped and {"trigger_distance_m": 5.0} in skipped
    assert result.minimal.changes == {"light": "night"}
    suite = [s.spec for s in app.rt.store.list(Scenario) if "regression" in s.tags]
    assert all(a.get("trigger", {}).get("ego_distance_m", 9) >= 7 for spec in suite
               for a in spec["actors"] if a["type"] == "worker")


async def test_stuck_car_case_becomes_a_box_test_the_fixed_release_passes(plant):
    from test_flow2_assistance import BOX_SCENE, stuck
    app = plant(["VIN-240"])
    await stuck(app, BOX_SCENE)
    await app.assistance.decide("AC-1", "wait_for_removal", by="supervisor-ana")
    request = app.test_requests[-1]
    result = app.generator.generate(request["text"], request["source"])
    assert result.ok, result.errors
    spec = ScenarioSpec.model_validate(result.scenario.spec)
    assert spec.actors[0].type.value == "box" and "reached_goal" not in spec.pass_criteria
    m = load_plant_map()
    for profile in RELEASES.values():
        run = simulate(spec, m, profile)
        assert judge(spec, run.metrics).passed and run.metrics["reached_goal"] == 0.0   # stops and waits
