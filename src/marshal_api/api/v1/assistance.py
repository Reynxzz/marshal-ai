"""Remote assistance desk endpoints (Flow 2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from marshal_api.api.v1 import _stubs
from marshal_api.core.deps import RequireRole, get_assistance_service
from marshal_api.core.idempotency import get_idempotency_key
from marshal_api.schemas import (
    AssistanceCase,
    AssistanceKpis,
    DecideAssistanceRequest,
    Page,
)
from marshal_api.services import AssistanceService

router = APIRouter(tags=["assistance"])

_supervisor = RequireRole(["supervisor"])


@router.get("/assistance/cases", response_model=Page[AssistanceCase])
async def list_cases(
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    _service: AssistanceService = Depends(get_assistance_service),
) -> Any:
    return _stubs.page(page=page, limit=limit)


@router.get("/assistance/cases/{case_id}", response_model=AssistanceCase)
async def get_case(case_id: str) -> AssistanceCase:
    return _stubs.assistance_case(case_id)


@router.get("/assistance/kpis", response_model=AssistanceKpis)
async def assistance_kpis() -> AssistanceKpis:
    return _stubs.assistance_kpis()


@router.post(
    "/assistance/cases/{case_id}/decide",
    response_model=dict,
    dependencies=[Depends(_supervisor), Depends(get_idempotency_key)],
)
async def decide_case(
    case_id: str, body: DecideAssistanceRequest
) -> dict[str, Any]:
    return {}
