import pytest

from marshal_ai.flows.report import ReworkStepDraft, bay_for, complete_plan
from marshal_ai.records import Car, CarReport, CarState, InspectionResult, Verdict


def car(app, vin):
    return app.rt.store.get(Car, vin)


async def test_dead_brake_light_rework_then_only_the_failed_step_is_re_run(plant):
    app = plant(["VIN-223"])
    await app.run_for(300)
    report = app.rt.store.get(CarReport, "VIN-223")
    assert report.verdict == Verdict.FAIL and "brake lights" in report.summary
    [proposal] = app.rt.pending("rework")
    assert [s["bay"] for s in proposal.payload["steps"]] == ["electrical"]
    assert car(app, "VIN-223").state == CarState.REPORT              # nothing moves before a human confirms

    await app.router.decide(proposal.proposal_id, confirm=True, by="supervisor-ana")
    await app.run_for(120)
    assert car(app, "VIN-223").location == "rework.electrical"
    assert app.rt.plant.bay_occupants["rework.electrical"] == ["VIN-223"]

    await app.router.repair_done("VIN-223", by="electrician-joe")
    assert app.rt.plant.bay_occupants["rework.electrical"] == []
    await app.run_for(300)
    functional = app.rt.store.get(InspectionResult, "VIN-223:functional")
    assert functional.verdict == Verdict.PASS
    report = app.rt.store.get(CarReport, "VIN-223")
    assert report.verdict == Verdict.PASS and report.rework[0].done_at is not None
    assert car(app, "VIN-223").state == CarState.YARD
    # only step 3 ran again: one track.step call more than the original six
    assert sum(c.task == "track.step" for c in app.rt.llm.calls) == 7


async def test_wrong_variant_goes_to_production_control_then_full_track(plant):
    app = plant(["VIN-231"])
    await app.run_for(60)
    [proposal] = app.rt.pending("rework")
    assert [s["bay"] for s in proposal.payload["steps"]] == ["production_control"]
    await app.router.decide(proposal.proposal_id, confirm=True, by="supervisor-ana")
    await app.run_for(200)
    assert car(app, "VIN-231").location == "production_control"
    await app.router.repair_done("VIN-231", by="production-control-lee")
    await app.run_for(600)
    assert app.rt.store.get(CarReport, "VIN-231").verdict == Verdict.PASS
    assert car(app, "VIN-231").state == CarState.YARD


async def test_supervisor_can_clear_findings(plant):
    app = plant(["VIN-223"])
    await app.run_for(300)
    [proposal] = app.rt.pending("rework")
    await app.router.decide(proposal.proposal_id, confirm=False, by="supervisor-ana")
    report = app.rt.store.get(CarReport, "VIN-223")
    assert report.verdict == Verdict.PASS and "cleared by supervisor-ana" in report.summary


async def test_full_bay_sends_car_to_yard_with_do_not_ship_hold(plant):
    app = plant(["VIN-223"])
    app.rt.plant.occupy("rework.electrical", "VIN-999")
    await app.run_for(300)
    [proposal] = app.rt.pending("rework")
    await app.router.decide(proposal.proposal_id, confirm=True, by="supervisor-ana")
    c = car(app, "VIN-223")
    assert c.do_not_ship and "rework_waiting" in c.flags
    decision = app.yard.decisions[-1]
    assert decision["kind"] == "rework_bay_full" and decision["status"] == "executed"
    await app.run_for(120)
    assert car(app, "VIN-223").location.startswith("yard.Y")
    assert app.yard.ship("T-07") == []                                # held car never loads
    app.rt.plant.release("VIN-999")
    await app.router._serve_waiting()
    await app.run_for(200)
    assert car(app, "VIN-223").location == "rework.electrical"


def test_plan_completion_adds_missed_defects_and_orders_safety_first():
    draft = [ReworkStepDraft(bay="paint", defect_type="scratch", reason="scratch on door")]
    required = [ReworkStepDraft(bay="paint", defect_type="scratch", reason="scratch"),
                ReworkStepDraft(bay="mechanical", defect_type="brakes", reason="brake test")]
    plan = complete_plan(draft, required)
    assert [s.bay for s in plan] == ["mechanical", "paint"]


def test_dispatch_refuses_a_rework_with_no_bays(app):
    """merge_verdict returns REVIEW when a result is missing, which sent a car with no failed
    checks down the rework path and crashed on tickets[0]."""
    import asyncio

    import pytest

    from marshal_ai.flows.report import _Plan

    app.router.plans["VIN-EMPTY"] = _Plan(tickets=[], visual_views=None, check_variant=False,
                                          functional_steps=[])

    with pytest.raises(ValueError, match="no bays to visit"):
        asyncio.run(app.router.dispatch("VIN-EMPTY"))


@pytest.mark.parametrize("defect, bay", [
    # the words Gemini wrote for findings in the evaluation runs, not the table's own spelling
    ("scratch", "paint"), ("scratches", "paint"), ("scuff", "paint"), ("dent", "paint"),
    ("paint_damage", "paint"), ("paint_transfer", "paint"), ("chip", "paint"), ("paint_peeling", "paint"),
    ("corrosion", "paint"), ("paint_run", "paint"),
    ("panel_gap", "body"), ("misalignment", "body"), ("missing_part", "body"), ("missing_badge", "body"),
    ("crack", "body"), ("body_damage", "body"),
    ("headlight_haze", "electrical"), ("brake_light", "electrical"), ("indicator", "electrical"),
    ("brakes", "mechanical"), ("steering", "mechanical"),
])
def test_a_defect_goes_to_the_bay_the_design_names(defect, bay):
    assert bay_for(defect) == bay


def test_the_electrical_bay_relights_a_dead_lamp_on_carla():
    from marshal_ai.driving.carla_backend import CarlaDrivingSystem
    ds = CarlaDrivingSystem.__new__(CarlaDrivingSystem)    # no server: only the fault table
    ds.faults = {"VIN-DARK": {"headlights"}, "VIN-OTHER": {"headlights"}}
    ds.repair("VIN-DARK", ["scratch"])
    assert ds.faults["VIN-DARK"] == {"headlights"}        # the paint bay does not touch lamps
    ds.repair("VIN-DARK", ["headlight", "electrical"])
    assert "VIN-DARK" not in ds.faults and ds.faults["VIN-OTHER"] == {"headlights"}
