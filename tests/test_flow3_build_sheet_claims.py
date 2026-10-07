"""The gate must not fail a car on something its build sheet never claimed.

Both of these were found against real gate frames from CARLA, on a car that was exactly what its
build sheet said it was:

  - "sunroof: build sheet says not present, car shows present (1.00)" - the sheet listed no
    options, and an empty options list was read as a claim that nothing is fitted;
  - "rear_badge: build sheet says none, car shows TESLA (1.00)" - on a Lincoln, at full
    confidence.

An options list says what a car is built with. It is not an exhaustive description of the car, so
absence from it is not a prohibition. The same run also showed why PASS was unreachable: a clean
car scored [0.9, 0.95, 0.9, 0.9] on its all-clears and went to review anyway.
"""

from __future__ import annotations

from marshal_ai.flows.visual import declared_items, visual_verdict
from marshal_ai.records import BuildSheet, Verdict


class _Mismatch:
    """Stands in for a Mismatch; visual_verdict only reads confidence."""

    def __init__(self, confidence: float):
        self.confidence = confidence


def test_declared_items_are_the_sheets_own_fields():
    sheet = BuildSheet(model="sedan", colour="blue", options=["Sunroof", "roof_rails"])

    assert declared_items(sheet) == {"colour", "model", "trim", "sunroof", "roof_rails"}


def test_an_option_the_sheet_omits_is_not_declared():
    """So a mismatch reported against it is dropped rather than failing the car."""
    sheet = BuildSheet(model="sedan", colour="blue")

    items = declared_items(sheet)

    assert "sunroof" not in items
    assert "rear_badge" not in items
    assert items == {"colour", "model", "trim"}, "nothing beyond what the sheet states"


def test_a_clean_car_at_the_confidence_bar_passes():
    """The confidences a real clean car actually scored at the gate."""
    verdict = visual_verdict([], [], [0.9, 0.95, 0.9, 0.9], variant_confidence=0.95)

    assert verdict is Verdict.PASS


def test_an_all_clear_below_the_bar_still_goes_to_review():
    assert visual_verdict([], [], [0.9, 0.85], 0.95) is Verdict.REVIEW
    assert visual_verdict([], [], [0.9, 0.9], 0.8) is Verdict.REVIEW


def test_a_confident_mismatch_still_fails_the_car():
    assert visual_verdict([], [_Mismatch(1.0)], [0.95], 0.95) is Verdict.FAIL


def test_a_borderline_mismatch_still_goes_to_review():
    assert visual_verdict([], [_Mismatch(0.7)], [0.95], 0.95) is Verdict.REVIEW
