"""Gemini as a decision model: one option number back, probabilities from its logprobs."""

from types import SimpleNamespace as NS

import pytest

from marshal_ai.llm import LLMError, decision_from_response, decision_schema


def _response(text, logprobs):
    top = [NS(token=t, log_probability=lp) for t, lp in logprobs.items()]
    return NS(text=text, candidates=[NS(logprobs_result=NS(top_candidates=[NS(candidates=top)]))])


def test_probabilities_come_from_the_logprobs_of_the_options_only():
    # a stray "[" took a third of the mass; the options are renormalised without it
    out = decision_from_response(_response("1", {"1": -0.44, "[": -1.12, "2": -4.43}), ["1", "2"], ("yes", "no"))
    assert out["choice"] == "yes"
    assert out["probs"]["yes"] == pytest.approx(0.982, abs=0.001)
    assert sum(out["probs"].values()) == pytest.approx(1.0)


def test_without_logprobs_the_answer_itself_is_the_choice():
    out = decision_from_response(NS(text="2", candidates=[]), ["1", "2"], ("yes", "no"))
    assert out == {"choice": "no", "probs": {"yes": 0.0, "no": 1.0}}


def test_an_answer_that_is_no_option_is_an_error():
    with pytest.raises(LLMError):
        decision_from_response(NS(text="maybe", candidates=[]), ["1", "2"], ("yes", "no"))


def test_decision_schemas_are_one_type_per_set_of_options():
    assert decision_schema("yes", "no") is decision_schema("yes", "no")
    assert decision_schema("yes", "no").OPTIONS == ("yes", "no")
    d = decision_schema("yes", "no")(choice="no", probs={"yes": 0.1, "no": 0.9})
    assert d.confidence == 0.9 and d.p("yes") == 0.1


async def test_every_still_frame_lamp_step_is_a_decision_with_no_sentence(plant):
    from marshal_ai.records import InspectionResult
    app = plant(["VIN-214", "VIN-223"])            # VIN-223's left brake light is dead
    from dataclasses import replace
    app.rt.settings = replace(app.rt.settings, decisions="track.decide")   # whatever the environment says
    await app.run_for(400)
    calls = app.rt.llm.calls
    passed = app.rt.store.get(InspectionResult, "VIN-214:functional")
    failed = app.rt.store.get(InspectionResult, "VIN-223:functional")
    assert passed.verdict.value == "PASS" and failed.verdict.value == "FAIL"
    # every still-frame step was a decision, the failed one too; no full answer was asked for
    assert sum(c.task == "track.decide" for c in calls) >= 2 * 4
    assert not any(c.task == "track.step" for c in calls if c.media and len(c.media) <= 2)
    assert any(i.camera_ok is False and "decision: no" in i.observation for i in failed.checklist)


def test_which_tasks_are_decisions_is_a_setting():
    from marshal_ai.flows.base import use_decisions
    rt = NS(settings=NS(decisions="track.decide"), llm=NS())
    assert use_decisions(rt, "track.decide") and not use_decisions(rt, "visual.screen")
    rt.settings.decisions = "all"
    assert use_decisions(rt, "visual.screen")
    rt.settings.decisions = "0"
    assert not use_decisions(rt, "track.decide")
    rt = NS(settings=NS(decisions="all"), llm=NS(handlers={"track.step": None}))
    assert not use_decisions(rt, "track.decide")      # a double that only answers in full
