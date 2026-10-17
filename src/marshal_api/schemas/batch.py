"""Request and response DTOs for batch runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from marshal_api.schemas.common import Page


class CreateBatchRequest(BaseModel):
    cars: int = Field(ge=1, le=6)
    seed: int | None = None


class BatchAccepted(BaseModel):
    batch_id: str
    job_id: str
    status: str
    requested_cars: int
    created_cars: int


class BatchSummary(BaseModel):
    batch_id: str
    status: str
    engine: str
    requested_cars: int
    created_cars: int
    finished_cars: int
    progress: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class BatchCarDto(BaseModel):
    vin: str
    truck_id: str | None = None
    expected: dict[str, Any] = Field(default_factory=dict)
    state: str
    stage: int
    location: str
    slot: str | None = None
    gate_verdict: str | None = None
    report_verdict: str | None = None
    updated_at: datetime


class Flow1Counts(BaseModel):
    released: int = 0
    waiting: int = 0
    in_yard: int = 0
    shipped: int = 0


class Flow2Counts(BaseModel):
    cases: int = 0
    open: int = 0
    resolved: int = 0
    escalated: int = 0


class VerdictCounts(BaseModel):
    pass_: int = Field(default=0, alias="pass")
    review: int = 0
    fail: int = 0

    model_config = {"populate_by_name": True}


class Flow4Counts(VerdictCounts):
    disagreements: int = 0


class Flow5Counts(BaseModel):
    pass_: int = Field(default=0, alias="pass")
    rework: int = 0
    shipped: int = 0

    model_config = {"populate_by_name": True}


class Flow6Counts(BaseModel):
    test_requests: int = 0
    generated: int = 0
    status: str = "not_triggered"


class Flow7Counts(BaseModel):
    runs: int = 0
    failures: int = 0
    status: str = "not_triggered"


class BatchFlowCounts(BaseModel):
    flow1: Flow1Counts = Field(default_factory=Flow1Counts)
    flow2: Flow2Counts = Field(default_factory=Flow2Counts)
    flow3: VerdictCounts = Field(default_factory=VerdictCounts)
    flow4: Flow4Counts = Field(default_factory=Flow4Counts)
    flow5: Flow5Counts = Field(default_factory=Flow5Counts)
    flow6: Flow6Counts = Field(default_factory=Flow6Counts)
    flow7: Flow7Counts = Field(default_factory=Flow7Counts)


class BatchEventDto(BaseModel):
    seq: int
    flow: int | None = None
    source: str
    title: str
    detail: str = ""
    level: str
    at: datetime


class BatchEvents(BaseModel):
    items: list[BatchEventDto]
    next_since: int
    has_more: bool


class BatchDetail(BatchSummary):
    message: str | None = None
    seed: int
    incident_enabled: bool
    incident_vin: str | None = None
    job_id: str
    flow_counts: BatchFlowCounts
    cars: list[BatchCarDto]
    report: dict[str, Any] | None = None


class BatchReport(BaseModel):
    batch_id: str
    status: str
    engine: str
    generated_at: datetime | str
    model: str
    batch_size: int
    counts: dict[str, Any]
    flow_kpis: dict[str, Any]
    labels: dict[str, Any]
    gemini: dict[str, Any]
    log: dict[str, Any]
    duration_s: float | None = None


class BatchCancelAccepted(BaseModel):
    batch_id: str
    status: str


BatchList = Page[BatchSummary]
