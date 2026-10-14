"""Sprint 0 gate: the runtime bridge provisions fallbacks, accepts injections and follows lifespan."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from marshal_ai.driving import MissionClient, make_driving_system
from marshal_ai.events import InMemoryBus
from marshal_ai.plant import load_plant_map
from marshal_ai.plant.state import PlantState

from marshal_api import bridge
from marshal_api import main as main_module


@pytest.fixture(autouse=True)
def clean_bridge():
    bridge.shutdown_bridge()
    yield
    bridge.shutdown_bridge()


def test_bridge_fallback_initialization() -> None:
    assert bridge.get_event_bus() is not None
    assert bridge.get_mission_client() is not None
    assert bridge.get_plant_state() is not None


def test_bridge_custom_injection() -> None:
    bus = InMemoryBus()
    plant_map = load_plant_map()
    client = MissionClient(make_driving_system("fake", plant_map), plant_map, bus=bus)
    state = PlantState()

    bridge.init_bridge(bus=bus, mission_client=client, plant_state=state)

    assert bridge.get_event_bus() is bus
    assert bridge.get_mission_client() is client
    assert bridge.get_plant_state() is state


def test_lifespan_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[bool] = []
    stopped: list[bool] = []
    monkeypatch.setattr(main_module, "init_bridge", lambda: started.append(True))
    monkeypatch.setattr(main_module, "shutdown_bridge", lambda: stopped.append(True))

    with TestClient(main_module.app):
        pass

    assert started == [True]
    assert stopped == [True]
