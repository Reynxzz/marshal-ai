import ast
import copy

import pytest

from marshal_ai.app import RELEASES
from marshal_ai.lab.adversarial import apply_variation, base_params
from marshal_ai.lab.compiler import compile_spec
from marshal_ai.lab.generator import EXAMPLE_SPEC, MAX_FIXES
from marshal_ai.lab.judge import judge
from marshal_ai.lab.runner import SimRunner
from marshal_ai.lab.sim import simulate
from marshal_ai.lab.spec import ScenarioSpec
from marshal_ai.llm import FakeLLM
from marshal_ai.plant import load_plant_map
from marshal_ai.records import Scenario, ScenarioStatus

DOC_REQUEST = ("A worker steps out from behind a parked forklift at dusk while a car turns "
               "into the finishing lane.")


def spec(**changes):
    raw = copy.deepcopy(EXAMPLE_SPEC)
    raw.update(changes)
    return ScenarioSpec.model_validate(raw)


# ---------- simulator and judge ----------

def test_simulator_is_repeatable_and_the_design_example_passes():
    m = load_plant_map()
    a, b = simulate(spec(), m), simulate(spec(), m)
    assert a.metrics == b.metrics
    assert judge(spec(), a.metrics).passed


def test_night_behind_a_pallet_stack_fails_the_gap():
    base = spec()
    worse = apply_variation(base, {"light": "night", "occluder": "pallet_stack",
                                   "trigger_distance_m": 7.0, "worker_speed_ms": 2.1}, "SC-0142-v1")
    j = judge(worse, simulate(worse, load_plant_map()).metrics)
    assert not j.passed and "min_gap_to_person_m >= 1.5" in j.failed


def test_variation_swaps_occluder_and_keeps_spec_valid():
    v = apply_variation(spec(), {"occluder": "none"}, "SC-0142-v2")
    assert [a.type.value for a in v.actors] == ["worker"] and v.actors[0].start == "finishing_corner.p3"
    v = apply_variation(spec(), {"occluder": "pallet_stack", "second_actor": "car_ahead_braking"}, "SC-0142-v3")
    assert {a.type.value for a in v.actors} == {"pallet_stack", "worker", "car"}
    assert base_params(v)["occluder"] == "pallet_stack"


def test_compiler_emits_valid_python_with_the_spec_embedded():
    code = compile_spec(spec(), load_plant_map())
    ast.parse(code)
    assert "BehaviorAgent" in code and "'finishing_corner.p3'" in code


# ---------- flow 6 ----------

def test_design_doc_request_becomes_a_dry_run_scenario_then_joins_the_library(app):
    result = app.generator.generate(DOC_REQUEST)
    assert result.ok, result.errors
    s = result.scenario.spec
    assert s["time_of_day"] == "dusk" and s["actors"][1]["start"] == "behind:forklift"
    assert result.dry_run_metrics["no_contact"] == 1.0 and result.code == app.runner.compile(ScenarioSpec.model_validate(s))
    assert app.generator.review(result.scenario.id, approve=True, engineer="eng-kim").status == ScenarioStatus.IN_LIBRARY


def test_fix_loop_feeds_validator_errors_back(app):
    bad = {**{k: v for k, v in EXAMPLE_SPEC.items() if k not in ("id", "source")}, "map_zone": "moon"}
    good = {k: v for k, v in EXAMPLE_SPEC.items() if k not in ("id", "source")}
    answers = iter([bad, good])
    app.rt.llm = FakeLLM({"scenario.spec": lambda call: next(answers)})
    result = app.generator.generate("anything")
    assert result.ok and result.attempts == 2
    assert "unknown zone 'moon'" in app.rt.llm.calls[1].prompt


def test_fix_loop_gives_up_after_three_fixes(app):
    app.rt.llm = FakeLLM({"scenario.spec": lambda call: {"map_zone": "moon"}})
    result = app.generator.generate("anything")
    assert not result.ok and result.attempts == MAX_FIXES + 1
    assert result.scenario.status == ScenarioStatus.REJECTED and "Needs an engineer" in result.scenario.notes


async def test_assistance_case_becomes_a_test(plant):
    from test_flow2_assistance import BOX_SCENE, stuck
    app = plant(["VIN-240"])
    await stuck(app, BOX_SCENE)
    await app.assistance.decide("AC-1", "wait_for_removal", by="supervisor-ana")
    request = app.test_requests[-1]
    result = app.generator.generate(request["text"], request["source"])
    assert result.ok, result.errors
    assert result.scenario.tags == ["from-incident"]


# ---------- flow 7 and the release gate ----------

@pytest.fixture
def library(app):
    result = app.generator.generate(DOC_REQUEST)
    app.generator.review(result.scenario.id, approve=True, engineer="eng-kim")
    return app, result.scenario.id


def test_search_finds_shrinks_explains_and_adds_a_regression_test(library):
    app, sid = library
    result = app.adversarial.search(sid)
    assert result.failure is not None and len(result.runs) <= 60
    assert len(result.minimal.changes) <= len(result.failure.changes)
    assert not result.minimal.run.passed
    assert result.explanation and result.explanation.suggested_fix
    reg = app.rt.store.get(Scenario, result.regression_id)
    assert reg.status == ScenarioStatus.IN_LIBRARY and "regression" in reg.tags
    # every run says why it was tried: the base case, the model's reasoning, or a shrinking step
    reasons = [r.run.reason for r in result.runs]
    assert reasons[0] == "the passing case the search starts from"
    assert reasons[1] == result.reasoning[0]
    assert all(r for r in reasons)
    shrinks = [r for r in reasons if r.startswith("shrinking")]
    assert len(shrinks) == (len(result.failure.changes) if len(result.failure.changes) > 1 else 0)


def test_search_keeps_going_when_the_model_is_unavailable(library):
    app, sid = library
    app.rt.llm = FakeLLM()                       # every call raises
    app.adversarial.budget = 40
    result = app.adversarial.search(sid)
    assert len(result.runs) <= 40 and all("random" in r for r in result.reasoning)


def test_release_gate_blocks_until_the_fix_ships(library):
    app, sid = library
    app.adversarial.search(sid)
    gate = app.release_gate
    old = gate.evaluate("behavior-agent-1.0", SimRunner(app.rt.map, RELEASES["behavior-agent-1.0"]))
    assert old.blocked and "release blocked" in old.summary()
    with pytest.raises(PermissionError):
        gate.approve(old, "eng-kim")
    new = gate.evaluate("behavior-agent-1.1", SimRunner(app.rt.map, RELEASES["behavior-agent-1.1"]))
    assert not new.blocked, new.failed
    assert new.newly_passing == sorted(old.failed)
    assert gate.approve(new, "eng-kim").approved_by == "eng-kim"


def test_the_failure_report_states_the_facts_and_tries_the_fix(library):
    app, sid = library
    result = app.adversarial.search(sid)
    report = result.failure_report
    assert report.measured and all("limit" in m or "contact" in m for m in report.measured)
    assert report.passes_when                    # the shrink established what makes it pass
    assert report.fix_check.startswith("Fix tried in the simulator") and "failing case passes" in report.fix_check
    stored = app.rt.store.get(Scenario, result.regression_id)
    assert stored.failure_report["fix_check"] == report.fix_check and stored.notes == result.report()


def _report_with(app, speed_fix):
    def explain(call):
        return {"summary": "s", "likely_cause": "c", "suggested_fix": "f", "speed_fix": speed_fix}
    app.rt.llm.on("adversarial.report", explain)


def test_a_fix_that_does_not_work_is_reported_as_such(library):
    app, sid = library
    _report_with(app, {"zone": "test_track", "time_of_day": ["night"], "max_speed_kmh": 4.0})  # wrong zone
    report = app.adversarial.search(sid).failure_report
    assert "still fails" in report.fix_check


def test_a_fix_in_a_zone_the_plant_lacks_is_not_tried(library):
    app, sid = library
    _report_with(app, {"zone": "paint_shop", "time_of_day": ["night"], "max_speed_kmh": 4.0})
    report = app.adversarial.search(sid).failure_report
    assert report.speed_fix is None and report.fix_check is None and report.suggested_fix == "f"
