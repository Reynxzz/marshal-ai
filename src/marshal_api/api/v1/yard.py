"""Yard operations and vehicle fleet endpoints (Flow 1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from marshal_api.api.v1 import _stubs
from marshal_api.core.deps import RequireRole, get_yard_service
from marshal_api.schemas import (
    CarDetail,
    CarSummary,
    Page,
    Yard,
    YardMap,
)
from marshal_api.services import YardService

router = APIRouter(tags=["yard"])

_supervisor = RequireRole(["supervisor"])


class TruckEtaRequest(BaseModel):
    eta: str
    reason: str | None = None


class ZoneClosureRequest(BaseModel):
    closed: bool
    reason: str | None = None


class MissionRequest(BaseModel):
    destination: str
    reason: str | None = None


class HoldRequest(BaseModel):
    reason: str
    duration_minutes: int | None = None


@router.get("/yard", response_model=Yard)
async def yard() -> Yard:
    return _stubs.yard()


@router.get("/cars", response_model=Page[CarSummary])
async def list_cars(
    q: str | None = None,
    state: str | None = None,
    location: str | None = None,
    truck: str | None = None,
    flags: list[str] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    _service: YardService = Depends(get_yard_service),
) -> Any:
    return _stubs.page(page=page, limit=limit)


@router.get("/cars/{vin}", response_model=CarDetail)
async def get_car(vin: str) -> CarDetail:
    return _stubs.car_detail(vin)


@router.get("/map", response_model=YardMap)
async def map_points() -> YardMap:
    return _stubs.yard_map()


@router.post("/trucks/{truck_id}/eta", response_model=dict, dependencies=[Depends(_supervisor)])
async def update_truck_eta(truck_id: str, body: TruckEtaRequest) -> dict[str, Any]:
    return {}


@router.post("/trucks/{truck_id}/load", response_model=dict, dependencies=[Depends(_supervisor)])
async def load_truck(truck_id: str) -> dict[str, Any]:
    return {}


@router.post("/zones/{zone}/closure", response_model=dict, dependencies=[Depends(_supervisor)])
async def set_zone_closure(zone: str, body: ZoneClosureRequest) -> dict[str, Any]:
    return {}


@router.post("/cars/{vin}/missions", response_model=dict, dependencies=[Depends(_supervisor)])
async def send_mission(vin: str, body: MissionRequest) -> dict[str, Any]:
    return {}


@router.post("/cars/{vin}/hold", response_model=dict, dependencies=[Depends(_supervisor)])
async def hold_car(vin: str, body: HoldRequest) -> dict[str, Any]:
    return {}


@router.post("/cars/{vin}/resume", response_model=dict, dependencies=[Depends(_supervisor)])
async def resume_car(vin: str) -> dict[str, Any]:
    return {}
