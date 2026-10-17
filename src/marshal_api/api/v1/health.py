"""System, identity and dev-actor endpoints."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from marshal_ai.records import Car
from marshal_ai.store import Store

from marshal_api.bridge import get_event_bus
from marshal_api.config import Settings, get_settings
from marshal_api.core.deps import get_current_actor, get_session, get_store
from marshal_api.schemas import Actor, Health, Readiness, Role
from marshal_api.schemas.common import (
    Dependency,
    DependencyStatus,
    HealthStatus,
    ReadinessStatus,
)

router = APIRouter(tags=["system"])

_START = time.monotonic()


class DevActorRequest(BaseModel):
    role: Role


async def _database_ok(session: AsyncSession) -> bool:
    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _store_ok(store: Store) -> bool:
    try:
        await asyncio.to_thread(store.list, Car)
        return True
    except Exception:
        return False


async def _tcp_ok(host: str, port: int, timeout_s: float = 0.2) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout_s
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


@router.get("/healthz", response_model=Health)
async def healthz() -> Health:
    return Health(status=HealthStatus.OK, uptime_s=round(time.monotonic() - _START, 1))


@router.get("/readyz", response_model=Readiness)
async def readyz(
    session: AsyncSession = Depends(get_session),
    store: Store = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> Readiness:
    if settings.store == "gcp":
        records = (
            DependencyStatus.OK if await _store_ok(store) else DependencyStatus.FAILED
        )
        primary = records
        dependencies = [Dependency(name="firestore", status=records)]
    else:
        database = (
            DependencyStatus.OK if await _database_ok(session) else DependencyStatus.FAILED
        )
        primary = database
        dependencies = [Dependency(name="database", status=database)]
    dependencies.append(
        Dependency(
            name="event-bus",
            status=DependencyStatus.OK if get_event_bus() is not None else DependencyStatus.FAILED,
        )
    )
    dependencies.append(
        Dependency(
            name="carla",
            status=(
                DependencyStatus.OK
                if await _tcp_ok(settings.carla_host, settings.carla_port)
                else DependencyStatus.FAILED
            ),
        )
    )
    return Readiness(
        status=ReadinessStatus.READY if primary == DependencyStatus.OK else ReadinessStatus.NOT_READY,
        dependencies=dependencies,
    )


@router.get("/me", response_model=Actor)
async def me(actor: Actor = Depends(get_current_actor)) -> Actor:
    return actor


@router.post("/dev/actor", response_model=Actor)
async def dev_actor(
    body: DevActorRequest,
    settings: Settings = Depends(get_settings),
) -> Actor:
    return Actor(
        actor_id=settings.demo_actor_id,
        display_name=body.role.value.title(),
        role=body.role,
        plant="Plant 01",
        permissions=[],
    )
