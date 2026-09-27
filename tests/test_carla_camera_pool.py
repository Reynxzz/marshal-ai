"""A pooled capture camera is created once and stays subscribed.

This file previously asserted the opposite - that a camera streams only between listen() and
stop() - on the reasoning that an idle camera producing every simulation frame at full
resolution is what pushed the CARLA server into "Invalid session: no stream available". That
reasoning was half right. Four gate cameras streaming 1280x720 at once really did overload the
server, but switching the subscription on and off per capture cannot work at all:

  - while the driving loop is running it is the only ticker, so a camera that subscribes in the
    middle of a capture has no tick of its own to wait for and the capture failed with "camera
    produced no frame after bounded warm-up" - which is what broke the track phase;
  - cycling the subscription also wedged the gate, logging "attempting to unsubscribe from
    stream but sensor wasn't listening" before the next destroy timed out and the server died.

The fix for the overload was to stop pointing four cameras at the gate - it now moves a single
camera - not to unsubscribe idle ones. What streams through this path is the track's pair,
which is the configuration that has always worked.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from marshal_ai.driving.carla_backend import CarlaDrivingSystem


class _Image:
    def __init__(self, tag: str):
        self.tag = tag

    def save_to_disk(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(self.tag.encode())


class _Camera:
    """A sensor that delivers frames to whatever is subscribed, as CARLA does."""

    def __init__(self, log: list[str]):
        self.log = log
        self.listening = False
        self._callback = None
        self.destroyed = False

    def listen(self, callback):
        self.log.append("listen")
        self.listening = True
        self._callback = callback

    def stop(self):
        self.log.append("stop")
        self.listening = False

    def destroy(self):
        self.log.append("destroy")
        self.destroyed = True

    def produce(self, tag: str = "frame") -> None:
        if self.listening and self._callback is not None:
            self._callback(_Image(tag))


class _World:
    def __init__(self, log: list[str], synchronous: bool = False):
        self.log = log
        self.cameras: list[_Camera] = []
        self._sync = synchronous

    def get_blueprint_library(self):
        bp = SimpleNamespace(set_attribute=lambda *a, **k: None,
                             has_attribute=lambda *a: False)
        return SimpleNamespace(find=lambda name: bp)

    def spawn_actor(self, blueprint, transform):
        self.log.append("spawn")
        camera = _Camera(self.log)
        self.cameras.append(camera)
        return camera

    def get_settings(self):
        return SimpleNamespace(synchronous_mode=self._sync)


def _backend(world) -> CarlaDrivingSystem:
    backend = CarlaDrivingSystem.__new__(CarlaDrivingSystem)
    backend.world = world
    backend._cameras = {}
    backend.frame_timeout_s = 0.05
    backend._task = None                    # no driving loop: this backend owns the ticks
    backend.vehicles = {}                   # cleanup also releases the cars' own cameras
    return backend


def _pose(x=1.0, y=2.0, z=3.0, yaw=4.0):
    return SimpleNamespace(location=SimpleNamespace(x=x, y=y, z=z),
                           rotation=SimpleNamespace(yaw=yaw))


def _frames_on_tick(backend, world, *tags: str):
    """Each tick delivers one frame, which is what a synchronous CARLA server does."""
    pending = list(tags)

    def tick():
        if pending and world.cameras:
            world.cameras[0].produce(pending.pop(0))
    backend._tick_world = tick


def test_a_capture_takes_a_fresh_frame_and_discards_the_ones_in_flight():
    log: list[str] = []
    world = _World(log, synchronous=True)
    backend = _backend(world)
    _frames_on_tick(backend, world, "stale-1", "stale-2", "wanted")

    data = backend.capture(_pose(), 640, 360)

    assert data == b"wanted", "frames already in flight still show the previous world state"


def test_the_camera_is_created_once_and_stays_subscribed():
    log: list[str] = []
    world = _World(log, synchronous=True)
    backend = _backend(world)
    _frames_on_tick(backend, world, *["f"] * 12)

    backend.capture(_pose(), 640, 360)
    backend.capture(_pose(), 640, 360)

    assert log.count("spawn") == 1, "spawning per frame is what crashed the server"
    assert log.count("listen") == 1, "subscribing per capture is what starved the track phase"
    assert "stop" not in log
    assert world.cameras[0].listening is True


def test_a_capture_while_the_driving_loop_runs_uses_a_frame_already_delivered():
    """It must not wait for a new frame, because it is the loop that would have to produce it.

    Track evidence is taken inside a handler the driving loop is waiting on. Waiting there for a
    freshly delivered frame deadlocks: the loop is blocked in this very call, so no tick happens
    and nothing arrives. Day 2 never hung because save_latest reads what the camera has already
    delivered, and this does the same.
    """
    log: list[str] = []
    world = _World(log, synchronous=True)
    backend = _backend(world)
    backend._task = object()                 # a running driving loop
    ticks: list[int] = []
    backend._tick_world = lambda: ticks.append(1)

    backend.prewarm_camera(_pose(), 640, 360)
    assert world.cameras[0].listening is True, "prewarm must subscribe, or no frame ever arrives"

    for tag in ("older", "newer", "newest"):   # delivered while the loop was ticking
        world.cameras[0].produce(tag)

    data = backend.capture(_pose(), 640, 360)

    assert data == b"newest", "the freshest frame the camera has already delivered"
    assert ticks == [], "the driving loop is the only thing allowed to tick"
    assert log.count("spawn") == 1, "prewarm must not be undone by the first capture"


def test_a_capture_during_the_loop_waits_only_if_nothing_has_arrived_yet():
    """With an empty queue there is nothing stale to prefer, so a short wait is correct."""
    world = _World([], synchronous=True)
    backend = _backend(world)
    backend._task = object()
    backend._tick_world = lambda: pytest.fail("the loop is the only ticker")
    backend.prewarm_camera(_pose(), 640, 360)

    threading.Timer(0.01, lambda: world.cameras[0].produce("first")).start()

    assert backend.capture(_pose(), 640, 360) == b"first"


def test_a_capture_that_never_gets_a_frame_says_so():
    log: list[str] = []
    world = _World(log, synchronous=True)
    backend = _backend(world)
    backend._tick_world = lambda: None       # ticks produce nothing

    with pytest.raises(RuntimeError, match="no frame"):
        backend.capture(_pose(), 640, 360)


def test_prewarm_creates_and_subscribes_the_sensor_up_front():
    """The server segfaults when a camera is created mid-tick, so runs create them up front."""
    log: list[str] = []
    world = _World(log)
    backend = _backend(world)

    backend.prewarm_camera(_pose(), 640, 360)

    assert log == ["spawn", "listen"]
    assert len(backend._cameras) == 1


def test_prewarm_is_idempotent():
    backend = _backend(_World([]))

    backend.prewarm_camera(_pose(), 640, 360)
    backend.prewarm_camera(_pose(), 640, 360)

    assert len(backend._cameras) == 1


def test_the_realtime_ratio_reports_simulated_seconds_per_real_second():
    backend = _backend(_World([]))
    backend._simulated_s = 0.0
    backend._sim_started_at = None

    assert backend.realtime_ratio() is None, "nothing measured yet"

    import time as _time
    backend._sim_started_at = _time.monotonic() - 2.0
    backend._simulated_s = 1.0

    ratio = backend.realtime_ratio()
    assert ratio is not None and 0.4 < ratio < 0.6, f"expected about 0.5, got {ratio}"


def test_close_cameras_stops_and_destroys_every_camera():
    log: list[str] = []
    world = _World(log)
    backend = _backend(world)

    backend.prewarm_camera(_pose(x=1.0), 640, 360)
    backend.prewarm_camera(_pose(x=99.0), 640, 360)   # a different pose gets its own camera

    backend.close_cameras()

    assert len(world.cameras) == 2
    assert all(camera.destroyed for camera in world.cameras)
    assert backend._cameras == {}
