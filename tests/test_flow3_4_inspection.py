from marshal_ai.flows.visual import Finding, Mismatch, visual_verdict
from marshal_ai.records import Car, CarState, InspectionResult, Verdict


def result(app, vin, kind):
    return app.rt.store.get(InspectionResult, f"{vin}:{kind}")


def test_visual_confidence_rules():
    scratch = lambda c: Finding(zone="rear_left_door", type="scratch", confidence=c)
    wrong = Mismatch(item="colour", expected="white", observed="red", confidence=0.96)
    assert visual_verdict([], [], [0.95, 0.96]) == Verdict.PASS
    assert visual_verdict([scratch(0.78)], [], [0.95]) == Verdict.REVIEW
    assert visual_verdict([scratch(0.95)], [], [0.95]) == Verdict.FAIL
    assert visual_verdict([], [wrong], [0.95]) == Verdict.FAIL
    assert visual_verdict([], [], [0.85]) == Verdict.REVIEW


async def test_clean_car_passes_the_gate_and_all_six_track_steps(plant):
    app = plant(["VIN-214"])
    await app.run_for(300)
    v, f = result(app, "VIN-214", "visual"), result(app, "VIN-214", "functional")
    assert v.verdict == Verdict.PASS and len(app.rt.llm.calls) > 0
    assert f.verdict == Verdict.PASS and [i.step for i in f.checklist] == [1, 2, 3, 4, 5, 6]


async def test_wrong_colour_fails_against_the_build_sheet(plant):
    app = plant(["VIN-231"])
    await app.run_for(60)
    v = result(app, "VIN-231", "visual")
    assert v.verdict == Verdict.FAIL
    assert any(m.startswith("colour: build sheet says white, car shows red") for m in v.build_sheet_mismatches)
    assert result(app, "VIN-231", "functional") is None            # a failed car does not run the track


async def test_scratch_goes_to_review_and_a_cleared_review_continues_to_the_track(plant):
    app = plant(["VIN-245"])
    await app.run_for(60)
    v = result(app, "VIN-245", "visual")
    assert v.verdict == Verdict.REVIEW
    assert v.findings[0].zone == "rear_left_door" and v.findings[0].image.endswith("left.jpg")
    [review] = app.rt.pending("review")
    await app.visual.decide_review(review.proposal_id, confirm_defect=False, by="supervisor-ana")
    assert app.rt.store.get(Car, "VIN-245").state == CarState.TEST_TRACK


async def test_dead_brake_lamp_telemetry_on_camera_off(plant):
    app = plant(["VIN-223"])
    await app.run_for(300)
    f = result(app, "VIN-223", "functional")
    assert f.verdict == Verdict.FAIL
    brake = next(i for i in f.checklist if i.name == "brake_lights")
    assert brake.telemetry_ok is True and brake.camera_ok is False
    assert all(i.passed for i in f.checklist if i.name != "brake_lights")
    assert app.functional.kpis()["telemetry_camera_disagreements"] == 1
