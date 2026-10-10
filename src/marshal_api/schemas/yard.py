"""Yard, car, truck, zone and map DTOs (Flow 1)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from marshal_api.schemas.common import CarState


class CarSummary(BaseModel):
    vin: str
    model: str | None = None
    color: str | None = None
    state: CarState
    location: str
    mission: str | None = None
    truck_id: str | None = None
    slot: str | None = None
    flags: list[str] = Field(default_factory=list)
    updated_at: datetime


class BuildSheet(BaseModel):
    model: str
    trim: str | None = None
    color: str
    options: list[str] = Field(default_factory=list)


class CarDetail(CarSummary):
    build_sheet: BuildSheet
    created_at: datetime


class Truck(BaseModel):
    truck_id: str
    scheduled_eta: datetime | None = None
    actual_eta: datetime | None = None
    delay_minutes: float | None = None
    destination: str | None = None
    assigned_vins: list[str] = Field(default_factory=list)


class Zone(BaseModel):
    zone: str
    name: str | None = None
    closed: bool = False
    reason: str | None = None


class ReworkBayStatus(str, Enum):
    OPEN = "open"
    NEAR_FULL = "near_full"
    FULL = "full"


class ReworkBay(BaseModel):
    bay_id: str
    name: str | None = None
    capacity: int = 0
    occupied: int = 0
    status: ReworkBayStatus | None = None
    active_vins: list[str] = Field(default_factory=list)


class ReworkBayList(BaseModel):
    items: list[ReworkBay] = Field(default_factory=list)


class YardSummary(BaseModel):
    total: int = 0
    exception: int = 0
    held: int = 0
    zones_closed: int = 0


class Yard(BaseModel):
    plant: str
    updated_at: datetime
    summary: YardSummary
    cars: list[CarSummary] = Field(default_factory=list)
    trucks: list[Truck] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    bays: list[ReworkBay] = Field(default_factory=list)


class MapPointKind(str, Enum):
    GATE = "gate"
    ZONE = "zone"
    BAY = "bay"
    ROUTE = "route"
    POINT = "point"


class MapPoint(BaseModel):
    id: str
    name: str
    kind: MapPointKind
    x: float | None = None
    y: float | None = None


class YardMap(BaseModel):
    points: list[MapPoint] = Field(default_factory=list)
