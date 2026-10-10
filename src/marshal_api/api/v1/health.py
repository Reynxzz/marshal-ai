"""System, identity and dev-actor endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from marshal_api.api.v1 import _stubs
from marshal_api.config import Settings, get_settings
from marshal_api.core.deps import get_current_actor
from marshal_api.schemas import Actor, Health, Readiness, Role

router = APIRouter(tags=["system"])


class DevActorRequest(BaseModel):
    role: Role


@router.get("/healthz", response_model=Health)
async def healthz() -> Health:
    return _stubs.health()


@router.get("/readyz", response_model=Readiness)
async def readyz() -> Readiness:
    return _stubs.readiness()


@router.get("/me", response_model=Actor)
async def me(actor: Actor = Depends(get_current_actor)) -> Actor:
    return actor


@router.post("/dev/actor", response_model=Actor)
async def dev_actor(
    body: DevActorRequest,
    settings: Settings = Depends(get_settings),
) -> Actor:
    return _stubs.actor(role=body.role, actor_id=settings.demo_actor_id)
