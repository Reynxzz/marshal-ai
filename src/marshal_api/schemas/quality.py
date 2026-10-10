"""Inspection, report and rework DTOs (Flows 3, 4 & 5)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from marshal_api.schemas.common import ReviewStatus, Verdict


class InspectionKind(str, Enum):
    VISUAL = "visual"
    FUNCTIONAL = "functional"


class InspectionResult(BaseModel):
    result_id: str
    vin: str
    kind: InspectionKind
    verdict: Verdict
    finding_count: int = 0
    failed_check_count: int = 0
    at: datetime
    review_status: ReviewStatus = ReviewStatus.PENDING
    proposal_id: str | None = None


class InspectionKpis(BaseModel):
    first_pass_yield: float = 0.0
    review_share: float = 0.0
    failure_count: int = 0
    period: str | None = None


class VisualFinding(BaseModel):
    zone: str
    defect_type: str
    size: str | None = None
    confidence: float
    observation: str
    media_id: str | None = None


class BuildSheetMismatch(BaseModel):
    attribute: str
    expected: str
    found: str
    confidence: float


class VisualInspection(BaseModel):
    result_id: str
    vin: str
    verdict: Verdict
    at: datetime
    review_status: ReviewStatus
    reviewed_by: str | None = None
    review_decision: str | None = None
    proposal_id: str | None = None
    findings: list[VisualFinding] = Field(default_factory=list)
    mismatches: list[BuildSheetMismatch] = Field(default_factory=list)


class FunctionalStep(BaseModel):
    step: int
    name: str
    telemetry: str | None = None
    camera_result: str | None = None
    confidence: float = 0.0
    observation: str | None = None
    disagree: bool = False
    media_id: str | None = None


class FunctionalInspection(BaseModel):
    result_id: str
    vin: str
    verdict: Verdict
    at: datetime
    review_status: ReviewStatus
    reviewed_by: str | None = None
    review_decision: str | None = None
    proposal_id: str | None = None
    steps: list[FunctionalStep] = Field(default_factory=list)


class ReworkTicketStatus(str, Enum):
    OPEN = "OPEN"
    IN_REPAIR = "IN_REPAIR"
    DONE = "DONE"
    WAITING_REINSPECTION = "WAITING_REINSPECTION"


class ReworkTicket(BaseModel):
    ticket_id: str
    vin: str
    kind: str
    bay: str
    status: ReworkTicketStatus
    created_at: datetime
    updated_at: datetime
    done_by: str | None = None


class ReinspectionStatus(str, Enum):
    WAITING = "WAITING"
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_REQUIRED = "NOT_REQUIRED"


class Report(BaseModel):
    vin: str
    verdict: Verdict
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    summary: str
    open_findings: int = 0
    visual_findings: list[VisualFinding] = Field(default_factory=list)
    functional_failures: list[FunctionalStep] = Field(default_factory=list)
    rework_tickets: list[ReworkTicket] = Field(default_factory=list)
    reinspection_status: ReinspectionStatus = ReinspectionStatus.NOT_REQUIRED
    updated_at: datetime
