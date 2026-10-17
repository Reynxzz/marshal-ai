"""Runtime bridge connecting marshal_api HTTP services to marshal_ai core domain objects."""

from __future__ import annotations

import logging

from marshal_ai.driving import MissionClient, make_driving_system
from marshal_ai.events import EventBus, make_bus
from marshal_ai.plant import PlantMap, load_plant_map
from marshal_ai.plant.state import PlantState
from marshal_ai.store import Store, make_store

from marshal_api.config import get_settings

log = logging.getLogger("marshal.bridge")

_bus: EventBus | None = None
_mission_client: MissionClient | None = None
_plant_state: PlantState | None = None
_store: Store | None = None


def is_live() -> bool:
    """Live mode reads the CARLA records from Firestore instead of the seeded database."""
    return get_settings().store == "gcp"


def init_bridge(
    bus: EventBus | None = None,
    mission_client: MissionClient | None = None,
    plant_state: PlantState | None = None,
    store: Store | None = None,
) -> None:
    """Initialize runtime singletons. Omitted components fall back to stand-ins."""
    global _bus, _mission_client, _plant_state, _store
    settings = get_settings()

    if bus is not None:
        _bus = bus
    elif _bus is None:
        log.info("Initializing %s bus", settings.bus)
        _bus = make_bus(settings.bus, settings.gcp_project, subscriber_name="api")

    if store is not None:
        _store = store
    elif _store is None:
        log.info("Initializing %s store", settings.store)
        _store = make_store(settings.store, settings.gcp_project)

    if plant_state is not None:
        _plant_state = plant_state
    elif _plant_state is None:
        _plant_state = PlantState()

    if mission_client is not None:
        _mission_client = mission_client
    elif _mission_client is None and not is_live():
        log.info("Initializing fallback fake MissionClient")
        plant_map: PlantMap = load_plant_map()
        backend = make_driving_system("fake", plant_map)
        _mission_client = MissionClient(backend, plant_map, bus=_bus)


def shutdown_bridge() -> None:
    """Clean up bridge resources during application shutdown."""
    global _bus, _mission_client, _plant_state, _store
    log.info("Shutting down runtime bridge")
    _bus = None
    _mission_client = None
    _plant_state = None
    _store = None


def get_event_bus() -> EventBus:
    if _bus is None:
        init_bridge()
    assert _bus is not None
    return _bus


def get_mission_client() -> MissionClient | None:
    if _mission_client is None and not is_live():
        init_bridge()
    # Live mode is read-only: there is no command channel into the CARLA plant, so no client.
    return _mission_client


def get_plant_state() -> PlantState:
    if _plant_state is None:
        init_bridge()
    assert _plant_state is not None
    return _plant_state


def get_store() -> Store:
    if _store is None:
        init_bridge()
    assert _store is not None
    return _store
