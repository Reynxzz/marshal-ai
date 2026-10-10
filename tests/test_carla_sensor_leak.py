"""Marshal must not leave cameras behind in the CARLA world.

Runs used to: 28 had built up in one session, each still rendering every frame on the GPU. There were
three ways in, and each test below closes one of them:

  - close_cameras released only a car's forward camera, so its front and rear evidence cameras
    outlived every run;
  - CARLA does not destroy a sensor when the car it is attached to is destroyed, and the demo
    scripts removed cars with a bare actor.destroy(), stranding all three;
  - a phase that ends without teardown - scene 1's gate on purpose, or any killed process - left its
    cameras for good, because nothing in a later process could tell Marshal's apart from anyone
    else's.
"""

from __future__ import annotations

from types import SimpleNamespace

from marshal_ai.driving.carla_backend import SENSOR_ROLE, CarlaDrivingSystem, _Vehicle


class _Actor:
    """A CARLA actor: an id, attributes, a parent, and whether it still exists."""

    _next_id = 100

    def __init__(self, world, type_id: str, role: str = "", parent=None):
        _Actor._next_id += 1
        self.id = _Actor._next_id
        self.type_id = type_id
        self.attributes = {"role_name": role}
        self.parent = parent
        self.is_listening = type_id.startswith("sensor.")
        self.world = world
        world.actors.append(self)

    def stop(self):
        self.is_listening = False

    def destroy(self):
        # Like CARLA: destroying a car does NOT destroy the sensors attached to it.
        self.world.actors.remove(self)


class _ActorList(list):
    def filter(self, pattern: str):
        prefix = pattern.rstrip("*")
        return _ActorList(a for a in self if a.type_id.startswith(prefix))


class _World:
    def __init__(self):
        self.actors: list[_Actor] = []

    def get_actors(self):
        return _ActorList(self.actors)

    def sensors(self):
        return [a for a in self.actors if a.type_id.startswith("sensor.")]


def _backend(world) -> CarlaDrivingSystem:
    backend = CarlaDrivingSystem.__new__(CarlaDrivingSystem)
    backend.world = world
    backend.vehicles = {}
    backend._cameras = {}
    return backend


def _car_with_cameras(world, backend, vin: str) -> _Vehicle:
    """A car as spawn() leaves it: three tagged cameras attached."""
    car = _Actor(world, "vehicle.lincoln.mkz_2020", role=vin)
    cam = lambda: _Actor(world, "sensor.camera.rgb", role=SENSOR_ROLE, parent=car)  # noqa: E731
    v = _Vehicle(vin=vin, actor=car, agent=None, location="line_end")
    v.onboard, v.onboard_frames = cam(), object()
    v.evidence = {"rear": (cam(), object()), "front": (cam(), object())}
    backend.vehicles[vin] = v
    return v


def test_close_cameras_releases_all_three_cameras_on_each_car():
    world = _World()
    backend = _backend(world)
    _car_with_cameras(world, backend, "VIN-1")
    _car_with_cameras(world, backend, "VIN-2")
    assert len(world.sensors()) == 6

    backend.close_cameras()

    assert world.sensors() == [], "the front and rear evidence cameras used to survive this"


def test_despawn_removes_the_car_and_every_camera_on_it():
    world = _World()
    backend = _backend(world)
    _car_with_cameras(world, backend, "VIN-1")

    backend.despawn("VIN-1")

    assert world.actors == []
    assert "VIN-1" not in backend.vehicles


def test_despawn_finds_a_car_left_by_an_earlier_process():
    """A new phase clears the previous phase's car, which this process never held a handle to."""
    world = _World()
    earlier = _backend(world)
    _car_with_cameras(world, earlier, "VIN-1")

    later = _backend(world)                    # a fresh process: knows nothing about VIN-1
    later.despawn("VIN-1")

    assert world.actors == [], "the car and its three cameras, found by role_name and parent"


def test_despawn_leaves_other_cars_alone():
    world = _World()
    backend = _backend(world)
    _car_with_cameras(world, backend, "VIN-1")
    _car_with_cameras(world, backend, "VIN-2")

    backend.despawn("VIN-1")

    assert len(world.sensors()) == 3
    assert [a.attributes["role_name"] for a in world.get_actors().filter("vehicle.*")] == ["VIN-2"]


def test_the_sweep_clears_marshal_sensors_and_nobody_elses():
    world = _World()
    _Actor(world, "sensor.camera.rgb", role=SENSOR_ROLE)      # a gate camera left on purpose
    _Actor(world, "sensor.camera.rgb", role=SENSOR_ROLE)      # one from a killed process
    viewer = _Actor(world, "sensor.camera.rgb", role="")      # the viewer service's camera
    replay = _Actor(world, "sensor.camera.rgb", role="replay")
    car = _Actor(world, "vehicle.lincoln.mkz_2020", role="VIN-9")

    cleared = _backend(world).sweep_sensors()

    assert cleared == 2
    assert set(world.actors) == {viewer, replay, car}, "only Marshal's own sensors are touched"


def test_a_backend_sweeps_when_it_connects(monkeypatch):
    """So a run starts clean however the previous one ended."""
    world = _World()
    _Actor(world, "sensor.camera.rgb", role=SENSOR_ROLE)

    from marshal_ai.driving import carla_backend

    monkeypatch.setattr(carla_backend, "carla", SimpleNamespace(Client=object))
    monkeypatch.setattr(carla_backend, "BehaviorAgent", object)
    monkeypatch.setattr(carla_backend, "connect_carla", lambda *a, **k: (
        SimpleNamespace(get_world=lambda: world), SimpleNamespace()))
    from marshal_ai.plant.map import load_plant_map

    CarlaDrivingSystem(load_plant_map())

    assert world.sensors() == []


def test_every_camera_blueprint_is_tagged():
    """Untagged cameras are the ones nothing can clear later."""
    attrs: dict[str, str] = {}
    bp = SimpleNamespace(set_attribute=lambda k, v: attrs.__setitem__(k, v),
                         has_attribute=lambda k: True)
    backend = _backend(_World())
    backend.world = SimpleNamespace(get_blueprint_library=lambda: SimpleNamespace(
        find=lambda name: bp))

    backend._camera_blueprint(1280, 720, fov=38.0)

    assert attrs["role_name"] == SENSOR_ROLE
    assert attrs["fov"] == "38.0"
