"""The release spacing rule must compare positions in the backend's own coordinate frame.

The plant map holds abstract coordinates (line_end is 0,0) while a real backend reports world
coordinates (line_end is 207.27,-13.93 on Town05). The rule compared the two, so for a car
standing still at the line end it computed 207.7 m, always read "clear", and released cars 16 m
apart on CARLA while the limit is 25. Every existing test passed because the fake backend also
reports plant coordinates, so the two frames happened to agree.
"""

from __future__ import annotations

import asyncio

import pytest

from marshal_ai.driving import Status
from marshal_ai.flows.yard import MIN_SPACING_M
from marshal_ai.records import BuildSheet, Car, CarState

# line_end on Town05, as the CARLA backend reports it
CARLA_LINE_END = (207.27, -13.93)


@pytest.fixture
def queue(app):
    """VIN-A released and heading to the gate, VIN-B still waiting behind it."""
    rt = app.rt
    rt.store.put(Car(vin="VIN-A", state=CarState.INSPECTION_GATE, location="line_end",
                     build_sheet=BuildSheet(model="sedan", colour="black"),
                     released_at=rt.clock(), mission_id="M-1"))
    rt.store.put(Car(vin="VIN-B", state=CarState.LINE_END, location="line_end",
                     build_sheet=BuildSheet(model="sedan", colour="black")))
    for vin in ("VIN-A", "VIN-B"):          # the backend needs the car to send it a mission
        rt.driving.spawn(vin, at="line_end")
    return app


def use_world_frame(app) -> None:
    app.rt.driving.reference_xy = lambda point: CARLA_LINE_END


def report(app, vin: str, x: float, y: float) -> None:
    """Put the car at a live position, which is what the rule now asks the backend for."""
    app.rt.driving.position_xy = lambda v, _vin=vin, _p=(x, y): _p if v == _vin else None


def report_status_only(app, vin: str, x: float, y: float) -> None:
    """No live position available, only a Status: the older path, still supported."""
    app.rt.driving.position_xy = lambda v: None
    app.rt.missions.last_status[vin] = Status(vin=vin, location="line_end", x=x, y=y,
                                             speed_kmh=5.0, mission_id="M-1", progress=0.3)


def test_the_reference_point_comes_from_the_backend(app):
    use_world_frame(app)

    assert app.yard.line_end_xy() == CARLA_LINE_END


def test_a_backend_without_a_frame_falls_back_to_the_plant_map(app):
    point = app.rt.map.points["line_end"]

    # the fake backend does expose one, and it is the plant map's own coordinates
    assert app.yard.line_end_xy() == (float(point.x), float(point.y))


def test_a_car_inside_the_spacing_is_not_clear(queue):
    """The case that shipped broken: 16.4 m out on CARLA still counted as clear."""
    use_world_frame(queue)
    report(queue, "VIN-A", x=207.1, y=-30.3)

    clear, why = queue.yard.first_segment_clear()

    assert clear is False
    assert "VIN-A" in why and f"{MIN_SPACING_M:.0f}" in why


def test_a_car_beyond_the_spacing_is_clear(queue):
    use_world_frame(queue)
    report(queue, "VIN-A", x=206.9, y=-45.0)        # about 31 m out

    clear, _why = queue.yard.first_segment_clear()

    assert clear is True


def test_release_waits_while_the_previous_car_is_too_close(queue):
    use_world_frame(queue)
    report(queue, "VIN-A", x=207.1, y=-30.3)

    allowed, why = queue.yard.can_release()

    assert allowed is False and "VIN-A" in why
    assert asyncio.run(queue.yard.release_next()) is None, "VIN-B must wait"


def test_release_proceeds_once_the_previous_car_is_clear(queue):
    use_world_frame(queue)
    report(queue, "VIN-A", x=206.9, y=-45.0)

    assert queue.yard.can_release()[0] is True
    assert asyncio.run(queue.yard.release_next()) == "VIN-B"


def test_a_status_is_used_when_the_backend_cannot_give_a_live_position(queue):
    use_world_frame(queue)
    report_status_only(queue, "VIN-A", x=207.1, y=-30.3)

    clear, why = queue.yard.first_segment_clear()

    assert clear is False and "VIN-A" in why


def test_with_neither_a_position_nor_a_status_it_falls_back_to_time(queue):
    """Last resort only. On CARLA this fallback let a car through after 18 real seconds while
    simulated time had moved it just 16 m, which is why a live position is preferred."""
    use_world_frame(queue)
    queue.rt.driving.position_xy = lambda vin: None
    queue.rt.missions.last_status.pop("VIN-A", None)

    clear, why = queue.yard.first_segment_clear()

    assert clear is False and "released less than" in why


def test_a_car_that_reached_the_gate_no_longer_blocks_the_segment(queue):
    """The gate is 20 m from the line end on Town05, so leaving the segment is how it clears."""
    use_world_frame(queue)
    car = queue.rt.store.get(__import__("marshal_ai.records", fromlist=["Car"]).Car, "VIN-A")
    queue.rt.store.put(car.model_copy(update={"location": "inspection_gate"}))
    report(queue, "VIN-A", x=207.1, y=-30.3)          # still only 16.4 m out

    clear, _why = queue.yard.first_segment_clear()

    assert clear is True
