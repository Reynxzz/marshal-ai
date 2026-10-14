"""Runtime bridge connecting marshal_api HTTP services to marshal_ai core domain objects."""

from __future__ import annotations

import logging

from marshal_ai.driving import MissionClient, make_driving_system
from marshal_ai.events import EventBus, InMemoryBus
from marshal_ai.plant import PlantMap, load_plant_map
from marshal_ai.plant.state import PlantState

log = logging.getLogger("marshal.bridge")

_bus: EventBus | None = None
_mission_client: MissionClient | None = None
_plant_state: PlantState | None = None


def init_bridge(
    bus: EventBus | None = None,
    mission_client: MissionClient | None = None,
    plant_state: PlantState | None = None,
) -> None:
    """Initialize runtime singletons. Omitted components fall back to in-memory stand-ins."""
    global _bus, _mission_client, _plant_state

    if bus is not None:
        _bus = bus
    elif _bus is None:
        log.info("Initializing fallback InMemoryBus")
        _bus = InMemoryBus()

    if plant_state is not None:
        _plant_state = plant_state
    elif _plant_state is None:
        log.info("Initializing default PlantState")
        _plant_state = PlantState()

    if mission_client is not None:
        _mission_client = mission_client
    elif _mission_client is None:
        log.info("Initializing fallback fake MissionClient")
        plant_map: PlantMap = load_plant_map()
        backend = make_driving_system("fake", plant_map)
        _mission_client = MissionClient(backend, plant_map, bus=_bus)


def shutdown_bridge() -> None:
    """Clean up bridge resources during application shutdown."""
    global _bus, _mission_client, _plant_state
    log.info("Shutting down runtime bridge")
    _bus = None
    _mission_client = None
    _plant_state = None


def get_event_bus() -> EventBus:
    if _bus is None:
        init_bridge()
    assert _bus is not None
    return _bus


def get_mission_client() -> MissionClient:
    if _mission_client is None:
        init_bridge()
    assert _mission_client is not None
    return _mission_client


def get_plant_state() -> PlantState:
    if _plant_state is None:
        init_bridge()
    assert _plant_state is not None
    return _plant_state
