"""The camera's recent past, which is what a clip is assembled from.

A clip cannot be taken by asking the camera for frames when the moment arrives. The stop, or the
routine step reporting in, is detected inside the driving loop, so the loop is blocked in the very
call that wants the frames and no further frame can arrive - that is the deadlock that failed with
"camera produced no frame after bounded warm-up". The frames have to be there already.

They cannot all be kept. A CARLA frame at 1280x720 is raw BGRA, about 3.7 MB, and the server makes
20 a second, so ten seconds would be some 700 MB per camera. Frames are kept at an interval spread
across the window instead, which is also what the judgement needs: a side changing or a path is read
from a handful of frames across the window, not from sixty consecutive ones.
"""

from __future__ import annotations

from marshal_ai.driving.carla_backend import (ASSIST_WINDOW_S, CLIP_WINDOW_S,
                                             INDICATOR_PERIOD_S, _FrameBuffer)
from marshal_ai.driving.routines import R12


def _stepping(step: float):
    """A clock that advances `step` seconds each time it is read."""
    ticks = iter(range(0, 10_000))
    return lambda: next(ticks) * step


def _buffer(window_s: float, max_frames: int, step: float):
    """A buffer whose clock advances `step` seconds per frame."""
    return _FrameBuffer(window_s=window_s, max_frames=max_frames, clock=_stepping(step))


def test_the_newest_frame_is_always_available():
    """A single-frame capture must show the world now, not the last sample kept."""
    buffer = _buffer(window_s=10.0, max_frames=8, step=0.05)

    for tag in ("a", "b", "c"):
        buffer.append(tag)

    assert buffer.latest() == "c", "even though b and c fall inside one sampling interval"


def test_frames_arriving_faster_than_the_interval_are_not_all_kept():
    """This is the point of the buffer: ten seconds of every frame would be some 700 MB."""
    buffer = _buffer(window_s=10.0, max_frames=8, step=0.05)   # 20 fps, interval 1.25 s

    for i in range(200):                                        # ten seconds of frames
        buffer.append(f"f{i}")

    kept = buffer.recent(8)
    assert len(kept) <= 8
    assert buffer.latest() == "f199"


def test_a_clip_is_returned_oldest_first():
    buffer = _buffer(window_s=3.0, max_frames=8, step=0.5)      # each frame is its own sample

    for tag in ("one", "two", "three"):
        buffer.append(tag)

    assert [image for _at, image in buffer.recent(8)] == ["one", "two", "three"]


def test_a_clip_is_spread_across_the_window_rather_than_bunched():
    buffer = _buffer(window_s=3.0, max_frames=8, step=0.5)
    for i in range(8):
        buffer.append(f"f{i}")

    picked = [image for _at, image in buffer.recent(3)]

    assert picked[0] == "f0", "the oldest held frame"
    assert picked[-1] == "f7", "and the newest"
    assert len(picked) == 3


def test_an_empty_buffer_reports_empty_rather_than_guessing():
    buffer = _buffer(window_s=3.0, max_frames=8, step=0.5)

    assert buffer.empty()
    assert buffer.recent(8) == []
    assert buffer.latest() is None


def test_samples_are_encoded_as_they_arrive_not_held_as_frames():
    """A CARLA frame belongs to its sensor's stream and is not safe to keep for seconds.

    Holding a handful for later encoding took the server down with a 20 s timeout. Encoding on
    arrival also turns 3.7 MB of raw BGRA into about 0.5 MB of JPEG.
    """
    encoded: list[str] = []

    def encode(image):
        encoded.append(image)
        return f"jpeg:{image}".encode()

    buffer = _FrameBuffer(window_s=3.0, max_frames=8, clock=_stepping(0.5), encode=encode)
    for tag in ("one", "two"):
        buffer.append(tag)

    assert [data for _at, data in buffer.recent(8)] == [b"jpeg:one", b"jpeg:two"]
    assert encoded == ["one", "two"], "encoded once each, when kept"


def test_frames_not_kept_are_never_encoded():
    """Encoding happens a few times a second, not twenty."""
    encoded: list[str] = []
    buffer = _FrameBuffer(window_s=10.0, max_frames=8, clock=_stepping(0.05),
                          encode=lambda image: encoded.append(image) or b"x")

    for i in range(40):                 # two seconds at 20 fps, interval is 1.25 s
        buffer.append(f"f{i}")

    assert len(encoded) <= 3, f"encoded {len(encoded)} of 40 frames"


def test_a_clip_covers_a_change_of_indicator_side():
    """The clip looks backwards from the step reporting in, so what it shows must happen often.

    The indicators step lasts 10 s and originally changed side once, at its midpoint: a clip of the
    last seconds showed one side steadily lit and a healthy car failed at confidence 1.0. Widening
    the window instead caught the car driving away afterwards, by which point its lamps were not
    resolvable. So the side alternates faster than the window is wide.
    """
    assert INDICATOR_PERIOD_S * 2 <= CLIP_WINDOW_S, \
        f"a {CLIP_WINDOW_S}s clip must contain both sides at a {INDICATOR_PERIOD_S}s period"


def test_the_track_window_does_not_reach_past_a_step():
    """Beyond the step the car drives off and the evidence becomes a speck in the distance."""
    shortest_step = min(s.duration_s for s in R12 if s.camera_demonstrable)

    assert CLIP_WINDOW_S <= shortest_step, \
        f"window {CLIP_WINDOW_S}s must fit inside a {shortest_step}s step"


def test_the_assist_window_is_what_the_design_asks_for():
    assert ASSIST_WINDOW_S == 10.0, "a 10 s clip before a stop"
