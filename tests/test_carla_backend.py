from types import SimpleNamespace
import threading

from marshal_ai.driving import carla_backend


class _Blueprint:
    def has_attribute(self, name):
        return False

    def set_attribute(self, name, value):
        pass


class _Actor:
    def __init__(self):
        self.synced = False
        self.destroyed = False

    def get_location(self):
        return "configured-spawn" if self.synced else "origin"

    def destroy(self):
        self.destroyed = True


class _World:
    def __init__(self, synchronous=False):
        self.actor = None
        self.settings = SimpleNamespace(synchronous_mode=synchronous)
        self.waited = False
        self.ticked = False

    def get_blueprint_library(self):
        return SimpleNamespace(find=lambda name: _Blueprint())

    def spawn_actor(self, blueprint, transform):
        self.actor = _Actor()
        return self.actor

    def get_settings(self):
        return self.settings

    def wait_for_tick(self, seconds):
        self.waited = True
        self.actor.synced = True

    def tick(self):
        self.ticked = True
        self.actor.synced = True


def _system(world):
    system = object.__new__(carla_backend.CarlaDrivingSystem)
    system.world = world
    system._tick_lock = threading.Lock()
    system.vehicle_blueprint = "vehicle.test"
    system.behavior = "cautious"
    system.vehicles = {}
    system.transform = lambda point: "transform"
    return system


def test_spawn_synchronizes_async_world_before_agent_construction(monkeypatch):
    observed = []
    monkeypatch.setattr(
        carla_backend,
        "BehaviorAgent",
        lambda actor, behavior: observed.append(actor.get_location()) or object(),
    )
    world = _World(synchronous=False)

    _system(world).spawn("VIN-1")

    assert world.waited
    assert observed == ["configured-spawn"]


def test_spawn_ticks_sync_world_before_agent_construction(monkeypatch):
    observed = []
    monkeypatch.setattr(
        carla_backend,
        "BehaviorAgent",
        lambda actor, behavior: observed.append(actor.get_location()) or object(),
    )
    world = _World(synchronous=True)

    _system(world).spawn("VIN-1")

    assert world.ticked
    assert observed == ["configured-spawn"]
