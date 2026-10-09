"""The physical plant and the cars moving through it."""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from marshal_api.db.session import DialectJSON
from marshal_api.models.base import (
    TenantScopedModel,
    enum_column,
    generate_ulid,
    utc_now,
)


class CarState(str, Enum):
    LINE_END = "line_end"
    INSPECTION_GATE = "inspection_gate"
    REVIEW = "review"
    TEST_TRACK = "test_track"
    REPORT = "report"
    REWORK = "rework"
    YARD = "yard"
    SHIPPED = "shipped"
    PARKED = "parked"
    DRIVING = "driving"
    HELD = "held"
    WAITING_DISPATCH = "waiting_dispatch"
    EXCEPTION = "exception"


class Zone(TenantScopedModel, table=True):
    __tablename__ = "zones"

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    code: str = Field(max_length=50, nullable=False)
    name: str = Field(max_length=100, nullable=False)


class ReworkBay(TenantScopedModel, table=True):
    __tablename__ = "rework_bays"

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    code: str = Field(max_length=50, nullable=False)
    name: str = Field(max_length=100, nullable=False)
    defect_type: Optional[str] = Field(default=None, max_length=50)
    capacity: int = Field(default=1)


class PlantMapPoint(TenantScopedModel, table=True):
    __tablename__ = "plant_map_points"

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    code: str = Field(max_length=50, nullable=False)
    name: str = Field(max_length=100, nullable=False)
    kind: str = Field(default="waypoint", max_length=30)
    x: float = Field(default=0.0)
    y: float = Field(default=0.0)
    z: float = Field(default=0.0)


class Truck(TenantScopedModel, table=True):
    __tablename__ = "trucks"

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    code: str = Field(max_length=50, nullable=False)
    status: str = Field(default="scheduled", max_length=30)
    capacity: int = Field(default=0)
    arrived_at: Optional[datetime] = Field(default=None)


class BuildSheet(SQLModel, table=True):
    __tablename__ = "build_sheets"
    __table_args__ = (
        # UNIQUE natively creates the required index for the 1:1 join.
        UniqueConstraint("car_id", name="uq_build_sheets_car_id"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    car_id: str = Field(foreign_key="cars.id", max_length=26, nullable=False)
    model: str = Field(max_length=50, nullable=False)
    trim: str = Field(default="standard", max_length=50)
    color: str = Field(max_length=50, nullable=False)
    options: List[str] = Field(default_factory=list, sa_column=Column(DialectJSON))

    car: Optional["Car"] = Relationship(back_populates="build_sheet")


class Car(TenantScopedModel, table=True):
    __tablename__ = "cars"
    __table_args__ = (
        # 1. VIN uniqueness within the tenant's plant (also a B-Tree index).
        UniqueConstraint(
            "tenant_id", "plant_id", "vin", name="uq_cars_tenant_plant_vin"
        ),
        # 2. Yard overview: (tenant_id, plant_id, state) is the left prefix.
        Index("idx_cars_tenant_plant_state", "tenant_id", "plant_id", "state"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    vin: str = Field(max_length=50, nullable=False)
    state: CarState = Field(
        default=CarState.LINE_END,
        sa_column=enum_column(CarState, length=30, default=CarState.LINE_END),
    )
    location: str = Field(default="line_end", max_length=50)
    mission_id: Optional[str] = Field(default=None, max_length=50)
    yard_slot: Optional[str] = Field(default=None, max_length=20)
    truck_id: Optional[str] = Field(
        default=None, foreign_key="trucks.id", max_length=26
    )
    do_not_ship: bool = Field(default=False)
    flags: List[str] = Field(default_factory=list, sa_column=Column(DialectJSON))
    released_at: Optional[datetime] = Field(default=None)
    yard_at: Optional[datetime] = Field(default=None)

    plant: Optional["Plant"] = Relationship(back_populates="cars")
    build_sheet: Optional[BuildSheet] = Relationship(
        back_populates="car", sa_relationship_kwargs={"uselist": False}
    )
    assistance_cases: List["AssistanceCase"] = Relationship(back_populates="car")


class Mission(TenantScopedModel, table=True):
    __tablename__ = "missions"

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    car_id: str = Field(foreign_key="cars.id", max_length=26, nullable=False)
    mission_type: str = Field(max_length=50, nullable=False)
    destination: str = Field(max_length=100, nullable=False)
    status: str = Field(default="issued", max_length=30)
    issued_at: datetime = Field(default_factory=utc_now, nullable=False)
    completed_at: Optional[datetime] = Field(default=None)
