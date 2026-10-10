"""Quality inspection, reports and rework endpoints (Flows 3, 4 & 5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from marshal_api.api.v1 import _stubs
from marshal_api.core.deps import RequireRole, get_quality_service
from marshal_api.schemas import (
    FunctionalInspection,
    InspectionKpis,
    InspectionResult,
    Page,
    Report,
    ReworkBayList,
    VisualInspection,
)
from marshal_api.services import QualityService

router = APIRouter(tags=["quality"])

_worker = RequireRole(["worker"])


class ReworkDoneRequest(BaseModel):
    actor_id: str


@router.get("/inspection/results", response_model=Page[InspectionResult])
async def list_results(
    kind: str | None = None,
    verdict: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    _service: QualityService = Depends(get_quality_service),
) -> Any:
    return _stubs.page(page=page, limit=limit)


@router.get("/inspection/kpis", response_model=InspectionKpis)
async def inspection_kpis() -> InspectionKpis:
    return InspectionKpis()


@router.get("/cars/{vin}/inspection/visual", response_model=VisualInspection)
async def visual_inspection(vin: str) -> VisualInspection:
    return _stubs.visual_inspection(vin)


@router.get("/cars/{vin}/inspection/functional", response_model=FunctionalInspection)
async def functional_inspection(vin: str) -> FunctionalInspection:
    return _stubs.functional_inspection(vin)


@router.get("/reports", response_model=Page[Report])
async def list_reports(
    verdict: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
) -> Any:
    return _stubs.page(page=page, limit=limit)


@router.get("/cars/{vin}/report", response_model=Report)
async def car_report(vin: str) -> Report:
    return _stubs.report(vin)


@router.get("/rework/bays", response_model=ReworkBayList)
async def rework_bays() -> ReworkBayList:
    return _stubs.rework_bays()


@router.post(
    "/cars/{vin}/rework/done",
    response_model=dict,
    dependencies=[Depends(_worker)],
)
async def rework_done(vin: str, body: ReworkDoneRequest) -> dict[str, Any]:
    return {}
