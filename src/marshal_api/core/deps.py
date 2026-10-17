"""Dependency-injection providers shared by the API routers.

``MARSHAL_STORE=gcp`` selects the read-only live services (Firestore); the default
``memory`` keeps the seeded-database demo services unchanged.
"""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import Depends, Header
from sqlmodel.ext.asyncio.session import AsyncSession

from marshal_ai.driving import MissionClient
from marshal_ai.events import EventBus
from marshal_ai.plant.state import PlantState
from marshal_ai.store import Store
from marshal_api.bridge import (
    get_event_bus,
    get_mission_client,
    get_plant_state,
    get_store,
)
from marshal_api.config import Settings, get_settings
from marshal_api.core.auth import RequireRole
from marshal_api.core.idempotency import get_idempotency_key
from marshal_api.db.session import async_session_maker
from marshal_api.live import (
    LiveAssistanceService,
    LiveAssistIngestService,
    LiveLabService,
    LiveMediaService,
    LiveProposalService,
    LiveQualityService,
    LiveYardService,
)
from marshal_api.schemas.common import Actor, Role
from marshal_api.services import (
    AssistIngestService,
    AssistanceService,
    LabService,
    MediaService,
    ProposalService,
    QualityService,
    YardService,
)
from marshal_api.services.batch_service import BatchService, get_batch_process_runner
from marshal_api.workers.job_runner import JobRunner, get_job_runner

__all__ = [
    "RequireRole",
    "get_assist_ingest_service",
    "get_batch_service",
    "get_assistance_service",
    "get_current_actor",
    "get_current_tenant",
    "get_event_bus",
    "get_idempotency_key",
    "get_job_runner",
    "get_lab_service",
    "get_llm",
    "get_media_service",
    "get_mission_client",
    "get_plant_state",
    "get_proposal_service",
    "get_quality_service",
    "get_session",
    "get_settings",
    "get_store",
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


def get_yard_service(
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    session: AsyncSession = Depends(get_session),
    mission_client: MissionClient | None = Depends(get_mission_client),
    event_bus: EventBus = Depends(get_event_bus),
    plant_state: PlantState = Depends(get_plant_state),
    tenant_id: str = Depends(get_current_tenant),
):
    if settings.store == "gcp":
        return LiveYardService(store)
    return YardService(session, mission_client, event_bus, plant_state, tenant_id)


def get_assistance_service(
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    session: AsyncSession = Depends(get_session),
    mission_client: MissionClient | None = Depends(get_mission_client),
    event_bus: EventBus = Depends(get_event_bus),
    tenant_id: str = Depends(get_current_tenant),
):
    if settings.store == "gcp":
        return LiveAssistanceService(store)
    return AssistanceService(session, mission_client, event_bus, tenant_id)


def get_proposal_service(
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    session: AsyncSession = Depends(get_session),
    event_bus: EventBus = Depends(get_event_bus),
    tenant_id: str = Depends(get_current_tenant),
):
    if settings.store == "gcp":
        return LiveProposalService(store)
    return ProposalService(session, event_bus, tenant_id)


def get_quality_service(
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    session: AsyncSession = Depends(get_session),
    mission_client: MissionClient | None = Depends(get_mission_client),
    event_bus: EventBus = Depends(get_event_bus),
    plant_state: PlantState = Depends(get_plant_state),
    tenant_id: str = Depends(get_current_tenant),
):
    if settings.store == "gcp":
        return LiveQualityService(store)
    return QualityService(session, mission_client, event_bus, plant_state, tenant_id)


def get_lab_service(
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    session: AsyncSession = Depends(get_session),
    job_runner: JobRunner = Depends(get_job_runner),
    tenant_id: str = Depends(get_current_tenant),
):
    if settings.store == "gcp":
        return LiveLabService(store)
    return LabService(session, job_runner, tenant_id)


def get_media_service(
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_current_tenant),
):
    if settings.store == "gcp":
        return LiveMediaService(settings)
    return MediaService(session, tenant_id)


def get_llm():
    """The Gemini client used by the clip-ingest service (Vertex/API key from the env)."""
    from marshal_ai.llm import GeminiLLM

    return GeminiLLM(get_settings().model)


def get_batch_service(
    settings: Settings = Depends(get_settings),
    tenant_id: str = Depends(get_current_tenant),
) -> BatchService:
    from marshal_ai.config import Settings as AgentSettings
    from marshal_ai.llm import GeminiLLM
    from marshal_api.services.batch_store import get_batch_store

    agent_settings = AgentSettings()
    if agent_settings.llm == "offline":
        from marshal_ai.offline import offline_llm

        llm = offline_llm()
    else:
        llm = GeminiLLM(settings.model)
    return BatchService(get_batch_store(settings), get_batch_process_runner(), tenant_id,
                        llm=llm, settings=settings, llm_mode=agent_settings.llm)


def get_assist_ingest_service(
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    session: AsyncSession = Depends(get_session),
    llm=Depends(get_llm),
    event_bus: EventBus = Depends(get_event_bus),
    tenant_id: str = Depends(get_current_tenant),
):
    if settings.store == "gcp":
        return LiveAssistIngestService(store)
    return AssistIngestService(session, llm, event_bus, tenant_id)
