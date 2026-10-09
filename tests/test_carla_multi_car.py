"""What has to hold for several cars to move through the plant at once.

Each test closes off something that broke, or would break, as soon as a second car was on the road:

  - a car waiting behind another plant car asked for help, as if it were blocked by an unknown object;
  - the gate could only photograph a parked car with the driving loop stopped, because its one camera
    has to be moved, and waiting for a frame from the new pose deadlocks a running loop;
  - three 1280x720 cameras per car rendering on every tick slowed simulated time to 0.47x real time
    with three cars and 0.28x with five;
  - the yard ranked slots on the schematic map, so the "nearest the loading bay" slot was 47 m from it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from marshal_ai.driving import carla_backend
from marshal_ai.driving.carla_backend import (CAMERA_TICK_S, GATE_VIEWS, CarlaDrivingSystem,
                                              CarlaGateCamera, _FrameBuffer, _Vehicle)
from marshal_ai.plant.map import load_plant_map


@pytest.fixture(autouse=True)
def _stub_carla(monkeypatch):
    if carla_backend.carla is None:
        monkeypatch.setattr(carla_backend, "carla",
                            SimpleNamespace(VehicleControl=lambda **kw: kw))


def _actor(type_id: str, role: str = ""):
    return SimpleNamespace(id=hash((type_id, role)) & 0xFFFF, type_id=type_id,
                           attributes={"role_name": role})


def _stalled_backend(obstacle, plant_vins=("VIN-1",)) -> tuple[CarlaDrivingSystem, _Vehicle]:
    """A backend whose car has been stopped with `obstacle` ahead for longer than the grace period."""
    backend = CarlaDrivingSystem.__new__(CarlaDrivingSystem)
    backend.auto_assist = True
    backend.blocked_speed_kmh = 0.6
    backend.blocked_after_s = 1.0
    backend.fixed_delta_s = 1.0
    backend.events = []
    backend.assist_options = ["wait_for_removal"]
    backend.raise_assist = lambda *a, **k: _record(backend, a)
    backend._obstacle_ahead = lambda v: (obstacle, 6.0)
    stopped = SimpleNamespace(get_velocity=lambda: SimpleNamespace(x=0.0, y=0.0, z=0.0))
    backend.vehicles = {vin: _Vehicle(vin=vin, actor=stopped, agent=None, location="gate")
                        for vin in plant_vins}
    v = backend.vehicles[plant_vins[0]]
    v.mission = SimpleNamespace(mission_id="M-1")
    return backend, v


async def _record(backend, args):
    """Like the real raise_assist: the car now has an open case, so it does not ask again."""
    backend.vehicles[args[0]].assist_case = args[1]
    backend.events.append(args)


def _stall(backend, v, seconds: int = 5) -> list:
    for _ in range(seconds):
        asyncio.run(backend._check_blocked(v))
    return backend.events


def test_waiting_behind_another_plant_car_is_queuing_not_being_stuck():
    car_ahead = _actor("vehicle.lincoln.mkz_2020", role="VIN-2")
    backend, v = _stalled_backend(car_ahead, plant_vins=("VIN-1", "VIN-2"))

    assert _stall(backend, v) == [], "no assistance card for a car queued behind the gate"


def test_a_vehicle_that_is_not_one_of_ours_still_raises_a_request():
    forklift_ish = _actor("vehicle.carlamotors.carlacola", role="")
    backend, v = _stalled_backend(forklift_ish)

    assert len(_stall(backend, v)) == 1


def test_a_person_still_raises_a_request_even_with_other_plant_cars_about():
    worker = _actor("walker.pedestrian.0022")
    backend, v = _stalled_backend(worker, plant_vins=("VIN-1", "VIN-2", "VIN-3"))

    assert len(_stall(backend, v)) == 1


def test_every_marshal_camera_renders_at_the_measured_rate():
    attrs: dict[str, str] = {}
    bp = SimpleNamespace(set_attribute=lambda k, v: attrs.__setitem__(k, v),
                         has_attribute=lambda k: True)
    backend = CarlaDrivingSystem.__new__(CarlaDrivingSystem)
    backend.camera_tick_s = CAMERA_TICK_S
    backend.world = SimpleNamespace(get_blueprint_library=lambda: SimpleNamespace(
        find=lambda name: bp))

    backend._camera_blueprint(1280, 720)

    assert attrs["sensor_tick"] == str(CAMERA_TICK_S)
    assert CAMERA_TICK_S <= 0.5, "a clip samples every 0.5 s, so frames must come at least that often"


class _Image:
    def __init__(self, tag: str):
        self.tag = tag


def _gate_backend(prewarmed: bool) -> tuple[CarlaDrivingSystem, list]:
    """A backend with the driving loop running, and fixed gate cameras that have been streaming."""
    backend = CarlaDrivingSystem.__new__(CarlaDrivingSystem)
    backend._task = object()                       # the driving loop is running
    backend.fixed_delta_s = 0.05
    backend.frame_timeout_s = 0.01
    backend.camera_tick_s = CAMERA_TICK_S
    backend._cameras = {}
    backend._encode = lambda image: image.tag.encode()
    backend.world = SimpleNamespace(get_settings=lambda: SimpleNamespace(synchronous_mode=True))
    backend._tick_world = lambda: pytest.fail("the gate must not tick a running loop")
    stored: list = []
    backend.store_artifact = lambda uri, data: stored.append(uri) or uri
    gate = CarlaGateCamera(backend)
    pose = {view: SimpleNamespace(location=SimpleNamespace(x=i, y=0.0, z=1.5),
                                  rotation=SimpleNamespace(yaw=0.0))
            for i, view in enumerate(GATE_VIEWS)}
    gate._pose = lambda view: pose[view]
    if prewarmed:
        for view in GATE_VIEWS:
            buffer = _FrameBuffer(window_s=1.0, max_frames=2)
            buffer.append(_Image(f"{view} now"))
            key = backend._camera_key(pose[view], 1280, 720)
            backend._cameras[key] = (object(), buffer, None)
    return backend, gate


def test_the_gate_photographs_a_car_while_the_loop_keeps_running():
    """Four fixed cameras have been streaming; the newest frame from each is the car at the gate."""
    backend, gate = _gate_backend(prewarmed=True)

    media = gate.capture("VIN-1")

    assert [m.label for m in media] == [f"{v} camera" for v in GATE_VIEWS]
    assert [m.data for m in media] == [f"{v} now".encode() for v in GATE_VIEWS]


def test_the_gate_refuses_to_create_cameras_inside_a_running_loop():
    """Spawning a camera mid-tick is what segfaults the server, so it must fail loudly instead."""
    backend, gate = _gate_backend(prewarmed=False)

    with pytest.raises(RuntimeError, match="prewarm"):
        gate.capture("VIN-1")


def test_the_nearest_slot_to_the_loading_bay_is_measured_where_the_cars_are():
    m = load_plant_map()
    slots = [p for p in m.points if p.startswith("yard.Y")]

    nearest = min(slots, key=lambda p: m.distance(p, "loading_bay"))

    assert nearest == "yard.Y8", "7 m from the bay in CARLA; the schematic said Y7, 47 m away"


class _Loc(SimpleNamespace):
    pass


def _creeping(along_m: float, obstacle=None):
    """A car facing +x, `along_m` short of a gate mark at the origin."""
    controls: list = []
    backend = CarlaDrivingSystem.__new__(CarlaDrivingSystem)
    backend.fixed_delta_s = 0.05
    backend.transform = lambda point: SimpleNamespace(location=_Loc(x=0.0, y=0.0))
    backend._obstacle_ahead = lambda v: (obstacle, 1.0) if obstacle else (None, 99.0)
    here = SimpleNamespace(location=_Loc(x=-along_m, y=0.0),
                           get_forward_vector=lambda: _Loc(x=1.0, y=0.0))
    actor = SimpleNamespace(get_transform=lambda: here,
                            get_velocity=lambda: _Loc(x=0.0, y=0.0, z=0.0),
                            apply_control=controls.append)
    v = _Vehicle(vin="VIN-1", actor=actor, agent=None, location="line_end")
    v.remaining = ["inspection_gate"]
    return backend, v, controls


def test_a_car_short_of_the_gate_mark_creeps_onto_it():
    """BehaviorAgent counts itself arrived 3-4 m out; the gate cameras are aimed at the mark."""
    backend, v, controls = _creeping(3.0)

    assert backend._creep(v) is False, "not there yet"
    assert controls[-1]["throttle"] > 0 and controls[-1]["brake"] == 0


def test_a_car_on_the_mark_stops_and_counts_as_arrived():
    backend, v, controls = _creeping(0.2)

    assert backend._creep(v) is True
    assert controls[-1]["brake"] == 1.0


def test_creeping_brakes_for_anything_in_front():
    backend, v, controls = _creeping(3.0, obstacle=_actor("walker.pedestrian.0001"))

    assert backend._creep(v) is False
    assert controls[-1]["brake"] == 1.0, "never edge forward into something"


def test_creeping_gives_up_rather_than_hold_up_the_plant():
    backend, v, controls = _creeping(3.0)
    v.creep_s = carla_backend.CREEP_MAX_S

    assert backend._creep(v) is True
