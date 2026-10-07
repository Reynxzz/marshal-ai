"""Arriving at the gate must be recorded when it happens, not when the verdict is ready.

Gate arrival used to be written only by the visual inspector, after the inspection finished.
A car standing at the gate therefore still reported location "line_end", and anything waiting
for the arrival rather than for the verdict waited forever - which is exactly what happened
when the flows were split into separate processes so the camera and the driving loop could not
collide. Where the car is and what has been decided about it are different facts.
"""

from __future__ import annotations

import asyncio

from marshal_ai.records import BuildSheet, Car, CarState
from marshal_ai.driving import MissionDone


def _car_at_gate(app) -> Car:
    """A car that has been released and has just reached the gate."""
    app.rt.store.put(Car(vin="VIN-G", state=CarState.INSPECTION_GATE, location="line_end",
                         build_sheet=BuildSheet(model="sedan", colour="black"),
                         released_at=app.rt.clock(), mission_id="M-9"))
    return app.rt.store.get(Car, "VIN-G")


def test_arrival_is_recorded_without_any_inspection(app):
    car = _car_at_gate(app)
    assert car.location == "line_end", "starts released but not yet arrived"

    asyncio.run(app.yard.on_mission_done(
        MissionDone(mission_id="M-9", vin="VIN-G", location="inspection_gate")))

    arrived = app.rt.store.get(Car, "VIN-G")
    assert arrived.location == "inspection_gate", "the car is at the gate, so it must say so"
    assert arrived.mission_id is None, "the mission that took it there is finished"


def test_arrival_does_not_decide_anything(app):
    """Arrival records position only. The verdict is still the inspector's to make."""
    _car_at_gate(app)
    asyncio.run(app.yard.on_mission_done(
        MissionDone(mission_id="M-9", vin="VIN-G", location="inspection_gate")))

    arrived = app.rt.store.get(Car, "VIN-G")
    assert arrived.state is CarState.INSPECTION_GATE, "state is unchanged by arriving"
    assert app.rt.store.get(type(arrived), "VIN-G").location == "inspection_gate"
