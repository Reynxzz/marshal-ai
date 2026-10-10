"""Dependency-injection providers shared by the API routers."""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import Depends, Header
from sqlmodel.ext.asyncio.session import AsyncSession

from marshal_api.config import Settings, get_settings
from marshal_api.core.auth import RequireRole
from marshal_api.core.idempotency import get_idempotency_key
from marshal_api.db.session import async_session_maker
from marshal_api.schemas.common import Actor, Role
from marshal_api.services import (
    AssistanceService,
    LabService,
    MediaService,
    ProposalService,
    QualityService,
    YardService,
)
from marshal_api.workers.job_runner import JobRunner
from marshal_api.workers.job_runner import get_job_runner as _build_job_runner

__all__ = [
    "RequireRole",
    "get_assistance_service",
    "get_current_actor",
    "get_current_tenant",
    "get_idempotency_key",
    "get_job_runner",
    "get_lab_service",
    "get_media_service",
    "get_proposal_service",
    "get_quality_service",
    "get_session",
    "get_settings",
    "get_yard_service",
]


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session


def get_current_tenant(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    settings: Settings = Depends(get_settings),
) -> str:
    return x_tenant_id or settings.demo_tenant_id


def get_current_actor(
    x_actor_id: str | None = Header(default=None, alias="X-Actor-ID"),
    x_actor_name: str | None = Header(default=None, alias="X-Actor-Name"),
    x_actor_role: str | None = Header(default=None, alias="X-Actor-Role"),
    settings: Settings = Depends(get_settings),
) -> Actor:
    role = x_actor_role or settings.default_role
    try:
        role_enum = Role(role)
    except ValueError:
        role_enum = Role.SUPERVISOR
    return Actor(
        actor_id=x_actor_id or settings.demo_actor_id,
        display_name=x_actor_name or settings.demo_actor_name,
        role=role_enum,
        permissions=[],
    )


def get_yard_service(session: AsyncSession = Depends(get_session)) -> YardService:
    return YardService(session)


def get_assistance_service(
    session: AsyncSession = Depends(get_session),
) -> AssistanceService:
    return AssistanceService(session)


def get_proposal_service(
    session: AsyncSession = Depends(get_session),
) -> ProposalService:
    return ProposalService(session)


def get_quality_service(session: AsyncSession = Depends(get_session)) -> QualityService:
    return QualityService(session)


def get_lab_service(session: AsyncSession = Depends(get_session)) -> LabService:
    return LabService(session)


def get_media_service(session: AsyncSession = Depends(get_session)) -> MediaService:
    return MediaService(session)


def get_job_runner() -> JobRunner:
    return _build_job_runner()
