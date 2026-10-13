"""A shift on the simulated plant: the design's operational KPIs, and that no faulty car ships."""

import asyncio

from marshal_ai.records import InspectionResult, Verdict
from marshal_ai.shift import Shift, ShiftConfig, summary


async def test_a_dead_lamp_is_caught_after_a_passing_car_became_the_reference(plant):
    """The second car is compared with the first car's frame; its dead brake lamp must still fail."""
    app = plant(["VIN-214", "VIN-223"])
    for _ in range(120):
        await app.run_for(10)
        if app.rt.store.get(InspectionResult, "VIN-223:functional"):
            break
    assert app.rt.store.get(InspectionResult, "VIN-214:functional").verdict == Verdict.PASS
    assert "brake_lights" in app.functional.references
    result = app.rt.store.get(InspectionResult, "VIN-223:functional")
    brake = next(i for i in result.checklist if i.name == "brake_lights")
    assert result.verdict == Verdict.FAIL and brake.telemetry_ok and brake.camera_ok is False


def _shift(**kw):
    return asyncio.run(Shift(ShiftConfig(**kw)).run())


def test_a_shift_ships_no_faulty_car_and_reports_every_kpi():
    k = _shift(cars=20, seed=3, stuck_rate=0.15)
    f5 = k["flow5_report"]
    assert f5["shipped_with_open_finding"] == 0 and f5["shipped_with_a_real_fault"] == []
    assert f5["clean_cars_sent_to_rework"] == []
    assert k["flow3_gate"]["missed"] == 0 and k["flow3_gate"]["false_alarms"] == 0
    assert k["flow4_track"]["telemetry_camera_disagreements"] >= k["flow4_track"]["cars_with_a_dead_checked_lamp"]
    assert k["flow2_assistance"]["wait_recommended_with_people"] == k["flow2_assistance"]["with_people"]
    assert k["flow1_yard"]["cars_per_hour_to_yard"] and k["flow1_yard"]["line_to_yard_min_clean"]["n"]
    assert "shipped with a real fault" in summary(k)


def test_defects_the_gate_misses_are_shipped_and_counted():
    """With every gate defect finding dropped, the damaged cars get through: the shift says which."""
    k = _shift(cars=20, seed=3, stuck_rate=0, miss_rate=1.0)
    escaped = k["flow5_report"]["shipped_with_a_real_fault"]
    assert k["flow3_gate"]["missed"] > 0 and escaped
    assert k["flow5_report"]["shipped_with_open_finding"] == 0       # the records say all was well
