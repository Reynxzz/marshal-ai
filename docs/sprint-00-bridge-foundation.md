# Sprint 0 — Runtime Bridge Foundation (`marshal_api.bridge`)

> **Target Phase**: Cross-Cutting Pre-requisite  
> **Target Files**:
> - `src/marshal_api/bridge.py` (New file)
> - `src/marshal_api/main.py` (FastAPI Lifespan updates)
> - `src/marshal_api/core/deps.py` (Dependency injection updates)
> - `tests/test_bridge.py` (New verification tests)

---

## 1. Overview & Objectives

Before domain services (`YardService`, `AssistanceService`, `ProposalService`) can interact with vehicles and physical plant infrastructure, the HTTP request layer in `marshal_api` must have a safe, thread-safe, and lifecycle-managed conduit into the core simulation and event infrastructure defined in `marshal_ai`.

### Core Responsibilities of the Bridge
1. **Singleton Management**: Provide access to `EventBus`, `MissionClient`, and `PlantState`.
2. **Graceful Fallbacks**: If CARLA or external simulation drivers are not active (e.g. running standalone local testing), provide simulated or in-memory stub clients to prevent 500 runtime crashes.
3. **Application Lifespan Hooks**: Initialize and tear down connections during FastAPI startup and shutdown.

---

## 2. Architecture & File Structure

```
marshal-ai/src/marshal_api/
├── bridge.py                # Global bridge holding runtime singletons & fallbacks
├── main.py                  # FastAPI lifespan registering bridge startup/teardown
└── core/
    └── deps.py              # FastAPI Depends() providers exposing bridge components
```

---

## 3. Implementation Details

### 3.1 `src/marshal_api/bridge.py`
```python
"""Runtime bridge connecting marshal_api HTTP services to marshal_ai core domain objects."""

from __future__ import annotations

import logging
from typing import Optional

from marshal_ai.driving.api import MissionClient
from marshal_ai.driving.fake import FakeDrivingBackend
from marshal_ai.events.bus import EventBus, InMemoryBus
from marshal_ai.plant import PlantMap
from marshal_ai.plant.state import PlantState

log = logging.getLogger("marshal.bridge")

_bus: Optional[EventBus] = None
_mission_client: Optional[MissionClient] = None
_plant_state: Optional[PlantState] = None


def init_bridge(
    bus: Optional[EventBus] = None,
    mission_client: Optional[MissionClient] = None,
    plant_state: Optional[PlantState] = None,
) -> None:
    """Initialize runtime singletons. If components are omitted, fallback mocks are created."""
    global _bus, _mission_client, _plant_state

    # 1. Initialize EventBus
    if bus is not None:
        _bus = bus
    elif _bus is None:
        log.info("Initializing fallback InMemoryBus")
        _bus = InMemoryBus()

    # 2. Initialize PlantState
    if plant_state is not None:
        _plant_state = plant_state
    elif _plant_state is None:
        log.info("Initializing default PlantState")
        _plant_state = PlantState()

    # 3. Initialize MissionClient
    if mission_client is not None:
        _mission_client = mission_client
    elif _mission_client is None:
        log.info("Initializing fallback FakeDrivingBackend MissionClient")
        fake_backend = FakeDrivingBackend()
        plant_map = PlantMap()
        _mission_client = MissionClient(fake_backend, plant_map, bus=_bus)


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
```

---

### 3.2 FastAPI Lifespan Integration (`src/marshal_api/main.py`)

Update the lifespan context manager in `main.py` to ensure the bridge starts and stops cleanly:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from marshal_api.bridge import init_bridge, shutdown_bridge

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB and Runtime Bridge
    init_bridge()
    yield
    # Shutdown: Clean up connections
    shutdown_bridge()

app = FastAPI(
    title="Marshal Control Tower API",
    lifespan=lifespan,
    # ...
)
```

---

### 3.3 Dependency Injection Integration (`src/marshal_api/core/deps.py`)

Add dependency providers so services and routes can cleanly inject bridge components without coupling:

```python
from fastapi import Depends
from marshal_ai.driving.api import MissionClient
from marshal_ai.events.bus import EventBus
from marshal_ai.plant.state import PlantState
from marshal_api.bridge import (
    get_event_bus as _get_event_bus,
    get_mission_client as _get_mission_client,
    get_plant_state as _get_plant_state,
)

def get_event_bus() -> EventBus:
    return _get_event_bus()

def get_mission_client() -> MissionClient:
    return _get_mission_client()

def get_plant_state() -> PlantState:
    return _get_plant_state()
```

---

## 4. Verification & Testing

### Test Suite (`tests/test_bridge.py`)
1. **`test_bridge_fallback_initialization`**: Verify that calling `get_event_bus()`, `get_mission_client()`, and `get_plant_state()` without prior initialization cleanly provisions in-memory fallback instances without raising exceptions.
2. **`test_bridge_custom_injection`**: Verify that initializing with custom mocked `EventBus` and `MissionClient` instances correctly overrides defaults.
3. **`test_lifespan_lifecycle`**: Use FastAPI `TestClient` context manager to assert `init_bridge` and `shutdown_bridge` trigger during startup/shutdown.

