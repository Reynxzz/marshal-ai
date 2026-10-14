"""Quality inspection, reports and rework endpoints (Flows 3, 4 & 5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

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

_worker = RequireRole(["worker", "supervisor"])


class ReworkDoneRequest(BaseModel):
    actor_id: str


@router.get("/inspection/results", response_model=Page[InspectionResult])
async def list_results(
    kind: str | None = None,
    verdict: str | None = None,
    review_status: str | None = None,
    vin: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: QualityService = Depends(get_quality_service),
) -> Page[InspectionResult]:
    return await service.list_results(kind, verdict, review_status, vin, page, limit)


@router.get("/inspection/kpis", response_model=InspectionKpis)
async def inspection_kpis(
    service: QualityService = Depends(get_quality_service),
) -> InspectionKpis:
    return await service.get_inspection_kpis()


@router.get("/cars/{vin}/inspection/visual", response_model=VisualInspection)
async def visual_inspection(
    vin: str, service: QualityService = Depends(get_quality_service)
) -> VisualInspection:
    return await service.get_visual(vin)


@router.get("/cars/{vin}/inspection/functional", response_model=FunctionalInspection)
async def functional_inspection(
    vin: str, service: QualityService = Depends(get_quality_service)
) -> FunctionalInspection:
    return await service.get_functional(vin)


@router.get("/reports", response_model=Page[Report])
async def list_reports(
    verdict: str | None = None,
    vin: str | None = None,
    bay: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: QualityService = Depends(get_quality_service),
) -> Page[Report]:
    return await service.list_reports(verdict, vin, bay, status, page, limit)


@router.get("/cars/{vin}/report", response_model=Report)
async def car_report(
    vin: str, service: QualityService = Depends(get_quality_service)
) -> Report:
    return await service.get_report(vin)


@router.get("/rework/bays", response_model=ReworkBayList)
async def rework_bays(
    service: QualityService = Depends(get_quality_service),
) -> ReworkBayList:
    return await service.get_rework_bays()


@router.post(
    "/cars/{vin}/rework/done",
    response_model=dict,
    dependencies=[Depends(_worker)],
)
async def rework_done(
    vin: str,
    body: ReworkDoneRequest,
    service: QualityService = Depends(get_quality_service),
) -> dict[str, Any]:
    return await service.complete_rework(vin, body.actor_id)
