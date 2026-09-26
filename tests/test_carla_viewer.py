import threading
import time

import pytest

from marshal_ai.carla_viewer import FrameBuffer, parser


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
