import threading
import time
from types import SimpleNamespace

import pytest

from marshal_ai.carla_viewer import FrameBuffer, _pose_from_arg, parser


class _FakeCarla:
    """Just enough of the carla module to check pose parsing without the client."""

    @staticmethod
    def Location(x, y, z):
        return SimpleNamespace(x=x, y=y, z=z)

    @staticmethod
    def Rotation(yaw):
        return SimpleNamespace(yaw=yaw)

    @staticmethod
    def Transform(location, rotation):
        return SimpleNamespace(location=location, rotation=rotation)


def test_camera_at_accepts_an_explicit_plant_pose():
    """Town05 has no spawn point near some plant routes, so poses must be given directly."""
    pose = _pose_from_arg(_FakeCarla, "193.5,-151.0,0.5,-118.0")

    assert (pose.location.x, pose.location.y, pose.location.z) == (193.5, -151.0, 0.5)
    assert pose.rotation.yaw == -118.0


@pytest.mark.parametrize("text", ["1,2,3", "1,2,3,4,5", "a,b,c,d", ""])
def test_camera_at_rejects_malformed_poses(text):
    with pytest.raises(SystemExit):
        _pose_from_arg(_FakeCarla, text)


def test_camera_at_defaults_to_none_so_spawn_index_is_used():
    assert parser().parse_args([]).camera_at is None


def test_frame_buffer_keeps_only_latest_frame():
    frames = FrameBuffer()
    frames.publish(b"first")
    frames.publish(b"second")

    sequence, jpeg = frames.wait_after(-1)

    assert sequence == 2
    assert jpeg == b"second"
    assert frames.status()["jpeg_bytes"] == len(b"second")


def test_frame_buffer_waits_for_next_frame():
    frames = FrameBuffer()

    def publish():
        time.sleep(0.01)
        frames.publish(b"next")

    thread = threading.Thread(target=publish)
    thread.start()
    assert frames.wait_after(0, timeout=0.5) == (1, b"next")
    thread.join()
    assert frames.wait_after(1, timeout=0.01) is None


def test_viewer_defaults_are_low_resource_and_local_only():
    args = parser().parse_args([])

    assert args.bind == "127.0.0.1"
    assert (args.width, args.height, args.fps, args.jpeg_quality) == (640, 360, 2.0, 60)
    assert args.http_port == 8080


@pytest.mark.parametrize("arguments", [["--fps", "0"], ["--jpeg-quality", "100"]])
def test_main_rejects_invalid_resource_settings(monkeypatch, arguments):
    from marshal_ai import carla_viewer

    with pytest.raises(SystemExit):
        carla_viewer.main(arguments)
