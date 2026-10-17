"""Shared DTO primitives: enums, pagination envelope, health and identity."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field


class Role(str, Enum):
    SUPERVISOR = "supervisor"
    INSPECTOR = "inspector"
    WORKER = "worker"
    ENGINEER = "engineer"


class Severity(str, Enum):
    INFO = "info"
    GOOD = "good"
    WARN = "warn"
    BAD = "bad"


class Verdict(str, Enum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    DONE = "DONE"
    ESCALATED = "ESCALATED"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class CarState(str, Enum):
    """The dashboard's operational states (distinct from the DB ``CarState``)."""

    PARKED = "parked"
    DRIVING = "driving"
    HELD = "held"
    WAITING_DISPATCH = "waiting_dispatch"
    INSPECTING = "inspecting"
    REWORK = "rework"
    READY = "ready"
    EXCEPTION = "exception"


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class ReadinessStatus(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"


class DependencyStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"


class KpiTrend(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class KpiTone(str, Enum):
    DEFAULT = "default"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    INFO = "info"


class Pagination(BaseModel):
    page: int
    limit: int
    total: int
    last_page: int


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    pagination: Pagination


class Health(BaseModel):
    status: HealthStatus
    uptime_s: float | None = None


class Dependency(BaseModel):
    name: str
    status: DependencyStatus


class Readiness(BaseModel):
    status: ReadinessStatus
    dependencies: list[Dependency] = Field(default_factory=list)


class Actor(BaseModel):
    actor_id: str
    display_name: str
    role: Role
    plant: str = "Plant 01"
    permissions: list[str] = Field(default_factory=list)


class Kpi(BaseModel):
    key: str
    label: str
    value: float
    unit: str | None = None
    period: str | None = None
    trend: KpiTrend | None = None
    tone: KpiTone | None = None


class KpiResponse(BaseModel):
    items: list[Kpi] = Field(default_factory=list)


class FlowEvent(BaseModel):
    seq: int
    at: datetime
    flow: int = Field(ge=1, le=7)
    source: str
    title: str
    detail: str
    level: Severity
    data: dict[str, Any] | None = None      # structured decision payload (choice, probs)


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Any | None = None
