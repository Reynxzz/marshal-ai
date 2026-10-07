"""Flow 4's camera half must be able to contradict healthy telemetry.

Measured on real CARLA night frames: asked in isolation whether headlights were lit in a
frame where they demonstrably were not, both gemini-2.5-flash and gemini-2.5-pro answered
"both headlights are lit" at confidence 1.0. Given a known-good reference frame of the same
step, the same model answered correctly at 0.99. These tests lock in the two properties that
made the difference: telemetry is never shown to the judge, and a reference is used once one
exists.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from marshal_ai.driving.routines import R12

import pytest

from marshal_ai.flows import functional as f
from marshal_ai.llm import Media


class _Recorder:
    """Captures what the camera judge was asked, and answers a scripted verdict."""

    def __init__(self, seen: bool, confidence: float = 0.99):
        self.seen, self.confidence = seen, confidence
        self.calls: list[dict] = []

    async def __call__(self, rt, task, prompt, schema, fallback, media, system):
        self.calls.append({"task": task, "prompt": prompt, "media": list(media or []),
                           "system": system})
        return f.StepSeen(seen=self.seen, confidence=self.confidence, observation="scripted"), True


def _checker(monkeypatch, recorder):
    monkeypatch.setattr(f, "ask_or", recorder)
    app = SimpleNamespace(rt=SimpleNamespace(track_camera=None))
    checker = f.FunctionalChecker.__new__(f.FunctionalChecker)
    checker.app = app
    checker.rt = app.rt
    checker.runs = {}
    checker._last_clips = {}
    checker.references = {}
    return checker


def _step():
    """The real routine step, so this cannot drift from what the flow actually reads."""
    return R12[0]                       # headlights: confirmable from a single frame


def _run(clip):
    return SimpleNamespace(vin="VIN-1", steps=[_step()],
                           reported={1: SimpleNamespace(telemetry={"headlights": "ON"})},
                           clips={1: clip})


def test_the_camera_judge_never_sees_telemetry(monkeypatch):
    """Telling the model what telemetry claimed made it confirm a dark lamp at confidence 1.0."""
    recorder = _Recorder(seen=True)
    checker = _checker(monkeypatch, recorder)
    clip = Media(mime="image/jpeg", data=b"frame", label="track")

    asyncio.run(checker._judge(_run(clip), _step()))

    prompt = recorder.calls[0]["prompt"]
    assert "ON" not in prompt, f"telemetry leaked into the camera prompt: {prompt!r}"
    assert "headlights" in prompt.lower()


def test_a_passing_step_becomes_the_reference_for_the_next_car(monkeypatch):
    recorder = _Recorder(seen=True)
    checker = _checker(monkeypatch, recorder)
    good = Media(mime="image/jpeg", data=b"lit", label="track")

    item = asyncio.run(checker._judge(_run(good), _step()))

    assert item.passed
    assert checker.references["headlights"] is good
    assert len(recorder.calls[0]["media"]) == 1, "the first car has nothing to compare against"


def test_once_a_reference_exists_it_is_sent_first_for_comparison(monkeypatch):
    recorder = _Recorder(seen=False)
    checker = _checker(monkeypatch, recorder)
    reference = Media(mime="image/jpeg", data=b"lit", label="reference")
    checker.references["headlights"] = reference
    dark = Media(mime="image/jpeg", data=b"dark", label="track")

    item = asyncio.run(checker._judge(_run(dark), _step()))

    media = recorder.calls[0]["media"]
    assert [m.data for m in media] == [b"lit", b"dark"], "reference must come first"
    assert media[0].label != media[1].label, "the two frames must be labelled differently"
    assert recorder.calls[0]["system"] is f.REFERENCE_SYSTEM
    # telemetry says ON, the camera says dark: the item must fail on the disagreement
    assert item.telemetry_ok is True
    assert item.camera_ok is False
    assert not item.passed


def test_a_failing_step_does_not_become_a_reference(monkeypatch):
    checker = _checker(monkeypatch, _Recorder(seen=False))

    asyncio.run(checker._judge(_run(Media(mime="image/jpeg", data=b"dark")), _step()))

    assert "headlights" not in checker.references


@pytest.mark.parametrize("confidence", [0.0, 0.3, 0.59])
def test_low_confidence_leaves_the_camera_undecided_rather_than_passing(monkeypatch, confidence):
    checker = _checker(monkeypatch, _Recorder(seen=True, confidence=confidence))

    item = asyncio.run(checker._judge(_run(Media(mime="image/jpeg", data=b"x")), _step()))

    assert item.camera_ok is None
    assert "headlights" not in checker.references
