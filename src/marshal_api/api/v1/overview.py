"""Control Tower overview and KPI endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from marshal_api.api.v1 import _stubs
from marshal_api.schemas import KpiResponse, Overview

router = APIRouter(tags=["overview"])


@router.get("/overview", response_model=Overview)
async def overview() -> Overview:
    return _stubs.overview()


@router.get("/kpis", response_model=KpiResponse)
async def kpis() -> KpiResponse:
    return _stubs.kpis()
