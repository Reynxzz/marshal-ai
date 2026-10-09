import importlib.util
import math
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).parents[1] / "scripts"


def _module(name):
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses look their module up here
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


def test_recordings_are_written_where_the_carla_service_may_write():
    recorder = _module("carla_recorder")

    assert recorder.recording_path("scene1-20261009-201500") == \
        "/var/lib/carla/recordings/scene1-20261009-201500.log"
    with pytest.raises(SystemExit):
        recorder.recording_path("../../etc/passwd")


def test_duration_is_read_from_recorder_file_info():
    recorder = _module("carla_recorder")
    info = "Version: 1\nMap: Town05\n...\nFrames: 7200\nDuration: 360.05 seconds\n"

    assert recorder.duration_s(info) == 360.05
    assert recorder.duration_s("File not found") is None


class _Actor:
    _ids = iter(range(100, 1000))

    def __init__(self, role):
        self.attributes = {"role_name": role}
        self.id = next(self._ids)


class _World:
    def __init__(self, roles):
        self._actors = [_Actor(r) for r in roles]

    def get_actors(self):
        world = self

        class _List(list):
            def filter(self, _pattern):
                return world._actors

        return _List()


def test_only_plant_and_lab_cars_are_candidates():
    replay = _module("replay_carla")
    world = _World(["autopilot", "VIN-Y1", "VIN-Y2", "marshal-lab"])

    names = sorted(replay.vin_cars(world))
    assert names[:2] == ["VIN-Y1", "VIN-Y2"]
    assert len(names) == 3 and names[2].startswith("marshal-lab-")


def _drive(director, frames):
    return [director.update(f) for f in frames]


def test_camera_cuts_to_whichever_car_starts_driving():
    replay = _module("replay_carla")
    director = replay.Director(grace_ticks=2)
    park = {"VIN-Y1": (0, 0, 0), "VIN-Y2": (50, 0, 0)}
    y1_moved = {"VIN-Y1": (1, 0, 0), "VIN-Y2": (50, 0, 0)}
    y2_moved = {"VIN-Y1": (1, 0, 0), "VIN-Y2": (51, 0, 0)}

    shots = _drive(director, [park, park, y1_moved, y1_moved, y2_moved])

    assert [keep for _, keep in shots] == [False, False, True, True, True]
    assert shots[2][0] == "VIN-Y1"
    assert shots[4][0] == "VIN-Y2"


def test_still_stretches_are_cut_after_a_short_grace():
    replay = _module("replay_carla")
    director = replay.Director(grace_ticks=2)
    frames = [{"VIN-Y1": (0, 0, 0)}, {"VIN-Y1": (1, 0, 0)}] + [{"VIN-Y1": (1, 0, 0)}] * 4

    keeps = [keep for _, keep in _drive(director, frames)]

    assert keeps == [False, True, True, True, False, False]
    assert all(keep for _, keep in _drive(replay.Director(keep_idle=True), frames))


def test_a_fixed_vin_is_followed_even_while_another_drives():
    replay = _module("replay_carla")
    director = replay.Director(follow="VIN-Y2", grace_ticks=0)
    frames = [{"VIN-Y1": (0, 0, 0), "VIN-Y2": (9, 0, 0)},
              {"VIN-Y1": (1, 0, 0), "VIN-Y2": (9, 0, 0)}]

    assert _drive(director, frames) == [("VIN-Y2", False), ("VIN-Y2", False)]


def test_a_teleport_is_not_driving():
    replay = _module("replay_carla")
    director = replay.Director(grace_ticks=0)
    frames = [{"VIN-Y1": (0, 0, 0)}, {"VIN-Y1": (0, 0, 40)}, {"VIN-Y1": (0, 0, 40)}]

    assert [keep for _, keep in _drive(director, frames)] == [False, False, False]


def test_opening_shot_stands_behind_the_whole_queue_looking_down_on_it():
    replay = _module("replay_carla")
    queue = [(0.0, 0.0, 0.0), (-9.0, 0.0, 0.0), (-18.0, 0.0, 0.0)]  # facing +x, VIN-Y3 last

    (x, y, z), pitch = replay.overview_pose(queue, yaw_deg=0.0)

    assert x < -18.0 and y == 0.0          # behind the last car, on the lane's axis
    assert 0.0 < z <= 30.0                 # raised, but never high over the whole map
    assert -60.0 < pitch < -15.0           # looking down at the cars


def test_each_drive_becomes_its_own_shot_and_long_drives_are_split():
    replay = _module("replay_carla")
    y1 = [("VIN-Y1", True, (0.0, -i * 1.0, 0.0)) for i in range(40)]   # 39 m: split once
    pause = [("VIN-Y1", False, (0.0, -39.0, 0.0))] * 5
    y2 = [("VIN-Y2", True, (5.0, -i * 1.0, 0.0)) for i in range(10)]

    shots = replay.plan_shots(y1 + pause + y2, max_len_m=30.0, min_ticks=1)

    assert [(s.vin, s.start, s.end) for s in shots] == [
        ("VIN-Y1", 0, 30), ("VIN-Y1", 31, 39), ("VIN-Y2", 45, 54)]
    assert len(shots[0].points) == 31


def test_side_shot_stands_beside_the_road_and_looks_at_the_drive():
    replay = _module("replay_carla")
    drive = [(0.0, -float(i), 0.0) for i in range(21)]   # 20 m heading -y

    (x, y, z), pitch, yaw = replay.side_pose(drive)

    assert abs(x) > 10.0                   # off to the side of the road
    assert -20.0 < y < 0.0                 # alongside the stretch, not at either end
    assert 0.0 < z < 15.0 and pitch < 0    # a little raised, looking down
    target_yaw = math.degrees(math.atan2(-10.0 - y, 0.0 - x))
    assert abs(yaw - target_yaw) < 1.0     # pointed at the middle of the drive


def test_a_car_settling_as_it_spawns_is_not_a_shot():
    replay = _module("replay_carla")
    settle = [("VIN-Y3", True, (0.0, 0.0, 0.5 - i * 0.05)) for i in range(6)]
    drive = [("VIN-Y1", True, (0.0, -i * 0.07, 0.0)) for i in range(40)]

    shots = replay.plan_shots(settle + drive)

    assert [(s.vin, s.start) for s in shots] == [("VIN-Y1", 6)]

