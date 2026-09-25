import pytest

from marshal_ai.demo import BOX_SCENE
from marshal_ai.driving import CommandRejected, MissionGo
from marshal_ai.events import Topic
from marshal_ai.flows.assistance import AssistAssessment, RankedOption, enforce_rules
from marshal_ai.records import AssistanceCase

OPTIONS = ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"]


async def stuck(app, scene):
    await app.rt.missions.send(MissionGo(mission_id="M-1", vin="VIN-240", waypoints=["yard.Y1"]))
    await app.run_for(10)
    app.rt.world.scenes["AC-1"] = scene
    await app.rt.driving.raise_assist("VIN-240", "AC-1", "unknown object in lane", OPTIONS,
                                      clip_uri="gs://marshal/assist/AC-1.mp4")
    return app.rt.store.get(AssistanceCase, "AC-1")


async def test_card_recommends_wait_when_a_worker_is_near(plant):
    app = plant(["VIN-240"])
    case = await stuck(app, BOX_SCENE)
    assert case.people_or_moving_equipment
    assert case.recommended == "wait_for_removal"
    assert "worker 3 m away" in case.situation
    assert {r["option"] for r in case.ranking} == set(OPTIONS)


async def test_card_may_recommend_passing_an_empty_lane(plant):
    app = plant(["VIN-240"])
    case = await stuck(app, {"object": "cardboard box", "where": "in lane", "distance_m": 4, "people": []})
    assert case.recommended == "pass_left_3kmh"


async def test_only_a_human_resolves_and_the_case_goes_to_the_test_lab(plant):
    app = plant(["VIN-240"])
    await stuck(app, BOX_SCENE)
    x = app.rt.driving.cars["VIN-240"].x
    await app.run_for(20)
    assert app.rt.driving.cars["VIN-240"].x == x                 # stays stopped until a human answers
    with pytest.raises(CommandRejected):
        await app.assistance.decide("AC-1", "drive_over_it", by="supervisor-ana")
    case = await app.assistance.decide("AC-1", "wait_for_removal", by="supervisor-ana")
    assert case.resolved_by == "supervisor-ana"
    request = app.rt.bus.history[Topic.TEST_REQUESTS][-1]
    assert request["case_id"] == "AC-1" and "wait_for_removal" in request["text"]
    await app.run_for(5)
    assert app.rt.driving.cars["VIN-240"].x != x


async def test_unanswered_case_escalates_after_60_seconds(plant):
    app = plant(["VIN-240"])
    await stuck(app, BOX_SCENE)
    await app.run_for(30)
    assert not app.rt.store.get(AssistanceCase, "AC-1").escalated
    await app.run_for(35)
    assert app.rt.store.get(AssistanceCase, "AC-1").escalated


def test_rules_override_the_model():
    a = AssistAssessment(situation="box", people_or_moving_equipment=True, confidence=0.9,
                         recommended="pass_left_3kmh",
                         ranking=[RankedOption(option="pass_left_3kmh", reason="fast", risk="low"),
                                  RankedOption(option="fly_over", reason="?", risk="low")])
    fixed, notes = enforce_rules(a, OPTIONS)
    assert fixed.recommended == "wait_for_removal"
    assert "fly_over" not in {r.option for r in fixed.ranking}
    assert fixed.confidence <= 0.5 and notes
