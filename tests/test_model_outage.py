"""When Gemini is unreachable every flow falls back to a safe outcome that leaves the decision to a person."""

from marshal_ai.llm import FakeLLM
from marshal_ai.records import AssistanceCase, Car, CarReport, CarState, InspectionResult, Verdict
from test_flow2_assistance import BOX_SCENE, stuck


def model_down(app):
    app.rt.llm = FakeLLM()                      # every call raises
    return app


async def test_clean_car_goes_to_human_review_instead_of_passing(plant):
    app = model_down(plant(["VIN-214"]))
    await app.run_for(60)
    visual = app.rt.store.get(InspectionResult, "VIN-214:visual")
    assert visual.verdict == Verdict.REVIEW
    assert app.rt.pending("review")


async def test_failed_car_still_gets_a_rework_proposal(plant):
    app = plant(["VIN-223"])
    app.rt.llm = FakeLLM({k: v for k, v in app.rt.llm.handlers.items() if k != "report.write"})
    await app.run_for(300)
    report = app.rt.store.get(CarReport, "VIN-223")
    assert report.verdict == Verdict.FAIL and "Written without Gemini" in report.summary
    [proposal] = app.rt.pending("rework")
    assert [s["bay"] for s in proposal.payload["steps"]] == ["electrical"]
    assert app.rt.store.get(Car, "VIN-223").state == CarState.REPORT     # nothing moves before a human


async def test_assist_card_recommends_only_waiting(plant):
    app = plant(["VIN-240"])
    model_down(app)
    case = await stuck(app, BOX_SCENE)
    assert isinstance(case, AssistanceCase)
    assert case.recommended == "wait_for_removal" and case.people_or_moving_equipment
    assert "model unavailable" in case.situation


async def test_yard_exception_waits_for_the_supervisor(plant):
    app = model_down(plant(["VIN-214"]))
    decision = await app.yard.handle_exception("track_closed", {"zone": "test_track"})
    assert decision["status"] == "needs_supervisor"
