import pytest

from marshal_ai.demo import BOX_SCENE
from marshal_ai.driving import CommandRejected, MissionGo
from marshal_ai.events import Topic
from marshal_ai.flows.assistance import AssistAssessment, RankedOption, SceneObject, enforce_rules
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
    # the card boxes the object in the way first, then the worker, on the clip it came with
    assert [o["kind"] for o in case.objects] == ["obstacle", "person"]
    assert all(o["image"] == "gs://marshal/assist/AC-1.mp4" and o["box_2d"] for o in case.objects)


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


def assessment(**kw) -> AssistAssessment:
    base = dict(situation="box in lane", people_or_moving_equipment=False, confidence=0.9,
                recommended="pass_left_3kmh",
                ranking=[RankedOption(option="pass_left_3kmh", reason="lane is empty", risk="low"),
                         RankedOption(option="wait_for_removal", reason="slow", risk="low")])
    return AssistAssessment(**{**base, **kw})


def test_a_person_the_model_boxed_counts_even_when_it_said_nobody_was_near():
    a = assessment(objects=[SceneObject(label="box", kind="obstacle", frame=0, box_2d=[500, 400, 700, 600]),
                            SceneObject(label="worker", kind="person", frame=0, box_2d=[300, 650, 700, 720])])
    fixed, notes = enforce_rules(a, OPTIONS, frames=1)
    assert fixed.people_or_moving_equipment
    assert fixed.recommended == "wait_for_removal"
    assert any("located" in n for n in notes)


def test_a_box_outside_the_images_is_dropped_but_the_object_kept():
    a = assessment(objects=[SceneObject(label="box", kind="obstacle", frame=0, box_2d=[700, 400, 500, 600]),
                            SceneObject(label="cone", kind="obstacle", frame=3, box_2d=[500, 400, 700, 600]),
                            SceneObject(label="pallet", kind="obstacle", frame=1, box_2d=[100, 100, 200, 1200])])
    fixed, notes = enforce_rules(a, OPTIONS, frames=2)
    assert [o.box_2d for o in fixed.objects] == [None, None, None]
    assert [o.label for o in fixed.objects] == ["box", "cone", "pallet"]
    assert fixed.recommended == "pass_left_3kmh"          # nothing hazardous: the model's pick stands


def test_a_card_without_a_clip_boxes_nothing():
    a = assessment(objects=[SceneObject(label="box", kind="obstacle", frame=0, box_2d=[500, 400, 700, 600])])
    fixed, _ = enforce_rules(a, OPTIONS)
    assert fixed.objects[0].box_2d is None


async def test_the_boxed_frame_is_stored_so_the_dashboard_can_draw_on_it(plant):
    from marshal_ai.driving import AssistRequest
    from marshal_ai.llm import Media
    app = plant(["VIN-240"])
    stored = {}

    def put(uri, data, mime="image/jpeg"):          # the uploader keeps the path, swaps the bucket
        stored[uri] = data
        return uri.replace("gs://marshal/", "gs://bucket/")

    app.rt.artifacts.put = put
    frames = [Media(mime="image/jpeg", data=f"frame {i}".encode(), uri=f"gs://marshal/assist/AC-9_f{i}.jpg")
              for i in (1, 2)]
    app.assistance.clip = lambda request: frames
    case = await app.assistance.on_request(AssistRequest(case_id="AC-9", vin="VIN-240", location="lane",
                                                         reason="object in lane", options=OPTIONS))
    assert case.objects and {o["image"] for o in case.objects} == {"gs://bucket/assist/AC-9_f1.jpg"}
    assert list(stored) == ["gs://marshal/assist/AC-9_f1.jpg"]     # frame 2 carries no box: not stored
