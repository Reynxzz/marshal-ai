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

from marshal_ai.driving.carla_backend import ASSIST_WINDOW_S, CLIP_WINDOW_S, _FrameBuffer


def _buffer(window_s: float, max_frames: int, step: float):
    """A buffer whose clock advances `step` seconds per frame."""
    ticks = iter(range(0, 10_000))
    return _FrameBuffer(window_s=window_s, max_frames=max_frames,
                        clock=lambda: next(ticks) * step)


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


def test_the_windows_match_what_the_design_asks_for():
    assert CLIP_WINDOW_S == 3.0, "a 3 s clip of each track step"
    assert ASSIST_WINDOW_S == 10.0, "a 10 s clip before a stop"
