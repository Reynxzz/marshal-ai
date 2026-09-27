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

import queue
from types import SimpleNamespace

from marshal_ai.driving.carla_backend import CarlaDrivingSystem


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
    frames: queue.Queue = queue.Queue(maxsize=4)
    for tag in tags:
        frames.put(_Image(tag))
    return SimpleNamespace(vin="VIN-1", onboard=object(), onboard_frames=frames)


def test_the_assist_frame_is_the_newest_the_car_has_delivered():
    backend = _backend()

    data = backend.assist_clip(_car_with_frames("older", "newest"))

    assert data == b"newest"


def test_a_car_without_a_camera_can_still_ask_for_help():
    """The camera is evidence, not part of driving, so its absence must not raise."""
    backend = _backend()
    car = SimpleNamespace(vin="VIN-2", onboard=None, onboard_frames=None)

    assert backend.assist_clip(car) is None


def test_no_frame_yet_is_reported_rather_than_raised():
    backend = _backend()
    car = SimpleNamespace(vin="VIN-3", onboard=object(), onboard_frames=queue.Queue())

    assert backend.assist_clip(car) is None, "an empty queue must not break the request"
