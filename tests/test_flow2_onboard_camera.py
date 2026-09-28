"""The frame behind an assistance request comes from a camera on the car.

It used to be taken by spawning a camera behind the car at the moment it stopped. That cannot
work. The stop is detected inside the driving loop, so the loop is blocked in the call that is
waiting for a frame only the loop could produce, and on the real server it failed every time with
"camera produced no frame after bounded warm-up". The failure was swallowed, so the request went
out carrying a gs:// URI that nothing had been uploaded to, and that surfaced much later as a 403
from Vertex AI trying to read it.

A camera on the car needs no pose chosen at the stop and no waiting.
"""

from __future__ import annotations

from types import SimpleNamespace

from marshal_ai.driving.carla_backend import CarlaDrivingSystem, _FrameBuffer


class _Image:
    def __init__(self, tag: str):
        self.tag = tag

    def save_to_disk(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(self.tag.encode())


def _backend() -> CarlaDrivingSystem:
    backend = CarlaDrivingSystem.__new__(CarlaDrivingSystem)
    backend.frame_timeout_s = 0.01
    backend._task = object()          # a running driving loop, as when a car stalls
    backend.world = SimpleNamespace(get_settings=lambda: SimpleNamespace(synchronous_mode=True))
    return backend


def _car_with_frames(*tags: str):
    """Frames arriving far enough apart to each be kept.

    The buffer holds eight samples across ten seconds, so it keeps one per 1.25 s and drops what
    falls between - that is the point of it, since keeping every frame of ten seconds would be
    some 700 MB. The clock here steps past that interval so each frame in the test is a sample.
    """
    ticks = iter(range(0, 1000, 2))
    frames = _FrameBuffer(window_s=10.0, max_frames=8, clock=lambda: next(ticks))
    for tag in tags:
        frames.append(_Image(tag))
    return SimpleNamespace(vin="VIN-1", onboard=object(), onboard_frames=frames)


def test_the_assist_clip_is_the_seconds_before_the_stop_in_order():
    """The design asks for a ten second clip with the request, oldest frame first."""
    backend = _backend()

    frames = backend.assist_clip(_car_with_frames("oldest", "middle", "newest"))

    assert frames == [b"oldest", b"middle", b"newest"], "in order, so a path can be read"


def test_the_last_frame_is_the_one_that_shows_what_stopped_the_car():
    backend = _backend()

    frames = backend.assist_clip(_car_with_frames("before", "at the stop"))

    assert frames[-1] == b"at the stop"


def test_a_car_without_a_camera_can_still_ask_for_help():
    """The camera is evidence, not part of driving, so its absence must not raise."""
    backend = _backend()
    car = SimpleNamespace(vin="VIN-2", onboard=None, onboard_frames=None)

    assert backend.assist_clip(car) == []


def test_no_frame_yet_is_reported_rather_than_raised():
    backend = _backend()
    car = SimpleNamespace(vin="VIN-3", onboard=object(),
                          onboard_frames=_FrameBuffer(window_s=10.0, max_frames=8))

    assert backend.assist_clip(car) == [], "an empty buffer must not break the request"
