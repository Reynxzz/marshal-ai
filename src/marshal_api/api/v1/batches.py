"""Create and monitor CARLA batch runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from marshal_api.core.deps import RequireRole, get_batch_service
from marshal_api.schemas import (
    BatchAccepted,
    BatchCancelAccepted,
    BatchDetail,
    BatchEvents,
    BatchReport,
    BatchSummary,
    CreateBatchRequest,
    Page,
)
from marshal_api.services.batch_service import BatchService

router = APIRouter(prefix="/batches", tags=["batches"])
_operator = RequireRole(["supervisor", "engineer"])


@router.post("", response_model=BatchAccepted, status_code=202,
             dependencies=[Depends(_operator)])
async def create_batch(body: CreateBatchRequest,
                       service: BatchService = Depends(get_batch_service)) -> BatchAccepted:
    return await service.create_batch(body.cars, body.seed)


@router.get("", response_model=Page[BatchSummary])
async def list_batches(
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: BatchService = Depends(get_batch_service),
) -> Page[BatchSummary]:
    return await service.list_batches(status, page, limit)


@router.get("/{batch_id}", response_model=BatchDetail)
async def get_batch(batch_id: str,
                    service: BatchService = Depends(get_batch_service)) -> BatchDetail:
    return await service.get_batch(batch_id)


@router.get("/{batch_id}/events", response_model=BatchEvents)
async def get_batch_events(
    batch_id: str,
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    flow: int | None = Query(default=None, ge=1, le=7),
    service: BatchService = Depends(get_batch_service),
) -> BatchEvents:
    return await service.get_events(batch_id, since, limit, flow)


@router.get("/{batch_id}/report", response_model=BatchReport)
async def get_batch_report(batch_id: str,
                           service: BatchService = Depends(get_batch_service)) -> BatchReport:
    return await service.get_report(batch_id)


@router.post("/{batch_id}/cancel", response_model=BatchCancelAccepted,
             status_code=202, dependencies=[Depends(_operator)])
async def cancel_batch(batch_id: str,
                       service: BatchService = Depends(get_batch_service)) -> BatchCancelAccepted:
    return await service.cancel(batch_id)
