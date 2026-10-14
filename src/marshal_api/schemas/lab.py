"""Test lab DTOs: requests, scenarios, adversarial results and releases."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TestRequestStatus(str, Enum):
    NEW = "new"
    PROCESSING = "processing"
    CONVERTED = "converted"
    FAILED = "failed"


class TestRequest(BaseModel):
    request_id: str
    source: str
    text: str
    created_at: datetime
    status: TestRequestStatus
    scenario_id: str | None = None


class ScenarioStatus(str, Enum):
    DRAFT = "draft"
    DRY_RUN_RUNNING = "dry_run_running"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    RUNNABLE = "runnable"


class Scenario(BaseModel):
    scenario_id: str
    source: str
    status: ScenarioStatus
    tags: list[str] = Field(default_factory=list)
    owner: str
    summary: str
    created_at: datetime


class ValidatorSeverity(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


class ValidatorNote(BaseModel):
    severity: ValidatorSeverity
    message: str


class ScenarioReview(BaseModel):
    decision: str
    actor_id: str
    note: str | None = None
    at: datetime


class AdversarialVerdict(str, Enum):
    ROBUST = "ROBUST"
    FRAGILE = "FRAGILE"


class VariantRun(BaseModel):
    variant_id: str
    light: str
    weather: str | None = None
    occluder: str | None = None
    trigger_distance_m: float | None = None
    ego_speed_kmh: float | None = None
    min_gap_m: float | None = None
    passed: bool = False
    clip_id: str | None = None


class AdversarialResult(BaseModel):
    runs: int
    verdict: AdversarialVerdict
    smallest_failing_variant: str | None = None
    criteria: dict[str, str] | None = None
    metrics: dict[str, str | float] | None = None
    report_id: str | None = None
    media_ids: list[str] = Field(default_factory=list)
    variants: list[VariantRun] = Field(default_factory=list)


class ScenarioDetail(Scenario):
    spec: dict[str, Any] = Field(default_factory=dict)
    validator_notes: list[ValidatorNote] = Field(default_factory=list)
    dry_run_metrics: dict[str, str | float] | None = None
    review: ScenarioReview | None = None
    adversarial_result: AdversarialResult | None = None


class ReleaseEvalTest(BaseModel):
    test_id: str
    scenario_id: str | None = None
    metric: str
    criteria: str
    result: str
    passed: bool
    report_id: str | None = None
    media_ids: list[str] | None = None


class ReleaseEvaluationStatus(str, Enum):
    NONE = "none"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ReleaseGate(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    BLOCKED = "blocked"


class Release(BaseModel):
    release_id: str
    evaluation_status: ReleaseEvaluationStatus
    gate: ReleaseGate
    passed: int = 0
    failed: int = 0
    tests: list[ReleaseEvalTest] = Field(default_factory=list)
    approved_by: str | None = None
    approved_at: datetime | None = None
    updated_at: datetime


class CreateScenarioRequest(BaseModel):
    situation: str
    source: str
    request_id: str | None = None


class ReviewScenarioRequest(BaseModel):
    decision: str
    actor_id: str
    note: str | None = None
