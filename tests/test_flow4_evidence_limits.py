"""A step a still cannot confirm must not be reported as a step that failed.

The whole routine was run against real CARLA for the first time, and three of the six steps failed
on a healthy car: indicators, brake test and full-lock turn. All three are asked of a single frame,
and none of them can be in one - a blinker is lit for only part of its cycle, and a stopping
distance and a turning circle are paths rather than states. Gemini answered "not seen" at
confidence 1.0, which was a fair reading of the frame and the wrong verdict for the car.

There are three cases, so there are three outcomes: a camera that contradicts telemetry is a
failure, evidence that could not settle it needs a person, and only a car where both sources agree
on every step passes unattended.
"""

from __future__ import annotations

from marshal_ai.driving.routines import R12, routine_steps
from marshal_ai.records import ChecklistItem, Verdict


def _verdict(items: list[ChecklistItem]) -> Verdict:
    """The rule in FunctionalChecker._record, over the checklist it builds."""
    if any(i.telemetry_ok is False or i.camera_ok is False for i in items):
        return Verdict.FAIL
    if any(i.camera_ok is None for i in items):
        return Verdict.REVIEW
    return Verdict.PASS


def test_the_steps_a_single_frame_cannot_show_are_marked_as_such():
    by_name = {s.name: s for s in R12}

    assert by_name["headlights"].still_confirmable, "a lit lamp is a state"
    assert by_name["brake_lights"].still_confirmable
    assert by_name["reverse"].still_confirmable, "the reverse lamp is a state"
    assert not by_name["indicators"].still_confirmable, "a blinker is lit part of the time"
    assert not by_name["brake_test"].still_confirmable, "a stopping distance is a path"
    assert not by_name["full_lock"].still_confirmable, "a turning circle is a path"


def test_an_unconfirmed_step_goes_to_review_not_to_rework():
    items = [
        ChecklistItem(step=1, name="headlights", telemetry_ok=True, camera_ok=True),
        ChecklistItem(step=2, name="indicators", telemetry_ok=True, camera_ok=None),
    ]

    assert _verdict(items) is Verdict.REVIEW, "nobody confirmed it; nobody contradicted it either"


def test_a_camera_that_contradicts_telemetry_still_fails():
    items = [
        ChecklistItem(step=1, name="headlights", telemetry_ok=True, camera_ok=False),
        ChecklistItem(step=2, name="indicators", telemetry_ok=True, camera_ok=None),
    ]

    assert _verdict(items) is Verdict.FAIL, "a disagreement outranks an open question"


def test_a_car_confirmed_on_every_step_passes():
    items = [ChecklistItem(step=s.step, name=s.name, telemetry_ok=True, camera_ok=True)
             for s in routine_steps("R-12", [1, 3, 4])]

    assert _verdict(items) is Verdict.PASS


def test_the_steps_demo_scene_1_runs_are_all_confirmable_from_a_frame():
    """So scene 1 can reach PASS unattended, which is the point of the scene."""
    assert all(s.still_confirmable for s in routine_steps("R-12", [1]))
