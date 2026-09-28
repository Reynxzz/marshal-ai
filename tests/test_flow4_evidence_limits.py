"""What the camera can and cannot settle for each track step.

Running the whole R-12 routine against real CARLA for the first time failed three of the six steps
on a healthy car. Looking into why turned up two faults in the simulation rather than in the
judgement:

  - the indicators step is "left, then right", but only `LeftBlinker` was ever set, so the right
    indicator was never energised and the step was never performed;
  - the car is held stationary for the length of a routine step, so the brake test and the
    full-lock turn do not happen at all - their telemetry is scripted.

So there are three kinds of step, and the checklist now says which is which: one a frame can settle,
one that needs the run of frames the design calls a 3 s clip, and one the simulation does not perform
at all, which rests on telemetry alone. The last matches the design's own scope - it says steps 1 to
4 can be demonstrated in CARLA.
"""

from __future__ import annotations

from marshal_ai.driving.routines import R12, routine_steps
from marshal_ai.records import ChecklistItem, Verdict


def _verdict(items: list[ChecklistItem]) -> Verdict:
    """The rule in FunctionalChecker._record, over the checklist it builds."""
    if any(i.telemetry_ok is False or i.camera_ok is False for i in items):
        return Verdict.FAIL
    if any(i.camera_ok is None and i.camera_applicable for i in items):
        return Verdict.REVIEW
    return Verdict.PASS


def _by_name() -> dict:
    return {s.name: s for s in R12}


def test_a_lit_lamp_is_settled_by_one_frame():
    steps = _by_name()

    for name in ("headlights", "brake_lights", "reverse"):
        assert steps[name].still_confirmable, f"{name} is a state a frame holds"
        assert steps[name].camera_demonstrable


def test_the_indicators_step_needs_a_sequence_but_is_performed():
    step = _by_name()["indicators"]

    assert not step.still_confirmable, "one frame cannot show the side changing"
    assert step.camera_demonstrable, "the car does energise each side in turn"
    assert "earlier frames" in step.expected_on_camera, "the expectation describes a sequence"


def test_the_steps_the_simulation_does_not_perform_are_marked_as_such():
    steps = _by_name()

    for name in ("brake_test", "full_lock"):
        assert not steps[name].camera_demonstrable, f"the car stands still through {name}"


def test_a_telemetry_only_step_passes_on_telemetry_alone():
    """Nothing happened for a camera to see, so its silence is not evidence against the car."""
    items = [
        ChecklistItem(step=1, name="headlights", telemetry_ok=True, camera_ok=True),
        ChecklistItem(step=5, name="brake_test", telemetry_ok=True, camera_ok=None,
                      camera_applicable=False),
    ]

    assert _verdict(items) is Verdict.PASS
    assert items[1].passed, "telemetry is all there is for this step"


def test_a_camera_that_could_have_looked_and_did_not_goes_to_review():
    items = [
        ChecklistItem(step=1, name="headlights", telemetry_ok=True, camera_ok=True),
        ChecklistItem(step=2, name="indicators", telemetry_ok=True, camera_ok=None),
    ]

    assert _verdict(items) is Verdict.REVIEW, "a step it could have settled and did not"


def test_a_camera_that_contradicts_telemetry_still_fails():
    items = [
        ChecklistItem(step=1, name="headlights", telemetry_ok=True, camera_ok=False),
        ChecklistItem(step=5, name="brake_test", telemetry_ok=True, camera_ok=None,
                      camera_applicable=False),
    ]

    assert _verdict(items) is Verdict.FAIL, "a disagreement outranks a step with no camera"


def test_a_telemetry_failure_fails_even_without_a_camera():
    items = [ChecklistItem(step=5, name="brake_test", telemetry_ok=False, camera_ok=None,
                           camera_applicable=False)]

    assert _verdict(items) is Verdict.FAIL


def test_the_steps_demo_scene_1_runs_are_all_settled_by_one_frame():
    """So scene 1 reaches PASS unattended, which is the point of the scene."""
    assert all(s.still_confirmable and s.camera_demonstrable
               for s in routine_steps("R-12", [1]))
