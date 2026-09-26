"""A pooled capture camera must not stream while nothing wants a frame.

Left listening, each camera produces every simulation frame at full resolution forever. With
the gate's four 1280x720 cameras that was enough to push the CARLA server into repeated
"Invalid session: no stream available" crashes - five restarts in one run - and to drag
simulated time behind real time, which in turn broke the yard spacing rule's wall-clock
fallback. Spawning per frame instead is what caused the crashes in the first place, so the
camera is kept and only its stream is switched on and off.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from marshal_ai.driving import carla_backend
from marshal_ai.driving.carla_backend import CarlaDrivingSystem


class _Image:
    def __init__(self, tag: str):
        self.tag = tag

    def save_to_disk(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(self.tag.encode())


class _Camera:
    """Streams only between listen() and stop(), like a real CARLA sensor."""

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


def stream_on_listen(*tags: str):
    """Make listen() immediately deliver these frames, as a live sensor would."""
    def listen(self, callback):
        self.log.append("listen")
        self.listening = True
        self._callback = callback
        for tag in tags:
            self.produce(tag)
    return listen


def _backend(world) -> CarlaDrivingSystem:
    backend = CarlaDrivingSystem.__new__(CarlaDrivingSystem)
    backend.world = world
    backend._cameras = {}
    backend.frame_timeout_s = 0.01          # keep the no-frame case instant
    return backend


def _pose(x=1.0, y=2.0, z=3.0, yaw=4.0):
    return SimpleNamespace(location=SimpleNamespace(x=x, y=y, z=z),
                           rotation=SimpleNamespace(yaw=yaw))


def test_a_capture_starts_the_stream_and_stops_it_again(monkeypatch):
    log: list[str] = []
    world = _World(log)
    backend = _backend(world)

    monkeypatch.setattr(_Camera, "listen", stream_on_listen("stale-1", "stale-2", "wanted"))

    data = backend.capture(_pose(), 640, 360)

    assert data == b"wanted", "the first two frames may be in flight from before"
    assert log == ["spawn", "listen", "stop"]
    assert world.cameras[0].listening is False, "an idle camera must not stream"


def test_a_second_capture_reuses_the_camera_rather_than_respawning(monkeypatch):
    log: list[str] = []
    world = _World(log)
    backend = _backend(world)

    monkeypatch.setattr(_Camera, "listen", stream_on_listen("a", "b", "c"))

    backend.capture(_pose(), 640, 360)
    backend.capture(_pose(), 640, 360)

    assert log.count("spawn") == 1, "spawning per frame is what crashed the server"
    assert log.count("listen") == 2 and log.count("stop") == 2
    assert len(world.cameras) == 1


def test_the_stream_is_stopped_even_when_no_frame_arrives():
    log: list[str] = []
    world = _World(log, synchronous=True)        # ticks instead of a long wait
    backend = _backend(world)
    backend._tick_world = lambda: None

    with pytest.raises(RuntimeError, match="no frame"):
        backend.capture(_pose(), 640, 360)       # nothing ever produces a frame

    assert log == ["spawn", "listen", "stop"], "a failed capture must not leave it streaming"


def test_close_cameras_stops_and_destroys_every_camera(monkeypatch):
    log: list[str] = []
    world = _World(log)
    backend = _backend(world)

    monkeypatch.setattr(_Camera, "listen", stream_on_listen("a", "b", "c"))
    backend.capture(_pose(x=1.0), 640, 360)
    backend.capture(_pose(x=99.0), 640, 360)     # a different pose gets its own camera

    backend.close_cameras()

    assert len(world.cameras) == 2
    assert all(camera.destroyed for camera in world.cameras)
    assert backend._cameras == {}
