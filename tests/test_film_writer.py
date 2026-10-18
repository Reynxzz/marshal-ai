"""MARSHAL_FILM cameras write CARLA's BGRA frames into an MP4 as they arrive (driving/carla_backend.py)."""

from types import SimpleNamespace

import numpy as np
import pytest

imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")

from marshal_ai.driving.carla_backend import FILM_SIZE, _FilmWriter  # noqa: E402


def _frame(bgra):
    h, w = FILM_SIZE[1], FILM_SIZE[0]
    a = np.zeros((h, w, 4), np.uint8)
    a[:, :] = bgra
    return SimpleNamespace(raw_data=a.tobytes(), height=h, width=w)


def test_frames_become_an_mp4_in_rgb_and_late_frames_are_ignored(tmp_path):
    writer = _FilmWriter(tmp_path / "car.mp4")
    for _ in range(10):
        writer.add(_frame((0, 0, 200, 255)))          # CARLA sends BGRA: this is red
    writer.close()
    writer.add(_frame((0, 0, 200, 255)))              # a callback after the car is gone
    assert writer.frames == 10
    frames = imageio_ffmpeg.read_frames(str(tmp_path / "car.mp4"))
    next(frames)
    first = np.frombuffer(next(frames), np.uint8).reshape(FILM_SIZE[1], FILM_SIZE[0], 3)
    r, g, b = first[FILM_SIZE[1] // 2, FILM_SIZE[0] // 2]
    assert r > 150 and g < 40 and b < 40
