"""Remote assistance desk endpoints (Flow 2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from marshal_api.core.deps import (
    RequireRole,
    get_assist_ingest_service,
    get_assistance_service,
    get_idempotency_key,
)
from marshal_api.schemas import (
    AssistanceCase,
    AssistanceKpis,
    DecideAssistanceRequest,
    Page,
)
from marshal_api.services import AssistIngestService, AssistanceService

router = APIRouter(tags=["assistance"])

_supervisor = RequireRole(["supervisor"])


class IngestAssistanceRequest(BaseModel):
    """A CARLA clip already in Cloud Storage, plus the options the car would offer."""

    clip_uri: str = Field(description="gs://bucket/object of the stored evidence")
    vin: str
    location: str = "unknown"
    options: list[str]
    situation: str | None = None


@router.get("/assistance/cases", response_model=Page[AssistanceCase])
async def list_cases(
    status: str | None = None,
    open: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: AssistanceService = Depends(get_assistance_service),
) -> Page[AssistanceCase]:
    return await service.list_cases(status, bool(open), page, limit)


@router.get("/assistance/cases/{case_id}", response_model=AssistanceCase)
async def get_case(
    case_id: str, service: AssistanceService = Depends(get_assistance_service)
) -> AssistanceCase:
    return await service.get_case(case_id)


@router.post(
    "/assistance/cases",
    response_model=AssistanceCase,
    status_code=201,
    dependencies=[Depends(_supervisor)],
)
async def ingest_case(
    body: IngestAssistanceRequest,
    ingest: AssistIngestService = Depends(get_assist_ingest_service),
    service: AssistanceService = Depends(get_assistance_service),
) -> AssistanceCase:
    """Assess a stored CARLA clip with Gemini and open the assistance case on the dashboard."""
    case_id = await ingest.ingest(
        clip_uri=body.clip_uri,
        vin=body.vin,
        location=body.location,
        options=body.options,
        situation=body.situation,
    )
    return await service.get_case(case_id)


@router.get("/assistance/kpis", response_model=AssistanceKpis)
async def assistance_kpis(
    service: AssistanceService = Depends(get_assistance_service),
) -> AssistanceKpis:
    return await service.get_kpis()


@router.post(
    "/assistance/cases/{case_id}/decide",
    response_model=dict,
    dependencies=[Depends(_supervisor), Depends(get_idempotency_key)],
)
async def decide_case(
    case_id: str,
    body: DecideAssistanceRequest,
    service: AssistanceService = Depends(get_assistance_service),
) -> dict[str, Any]:
    return await service.decide_case(case_id, body.option, body.actor_id)
