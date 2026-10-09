"""Inspection outcomes, findings and rework."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from marshal_api.models.base import TenantScopedModel, enum_column, generate_ulid


class Verdict(str, Enum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


class InspectionResult(TenantScopedModel, table=True):
    __tablename__ = "inspection_results"
    __table_args__ = (
        UniqueConstraint("result_id", name="uq_results_result_id"),
        Index("idx_results_car_id", "car_id"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    result_id: str = Field(max_length=100, nullable=False)
    car_id: str = Field(foreign_key="cars.id", max_length=26, nullable=False)
    kind: str = Field(max_length=20, nullable=False)  # visual | functional
    verdict: Verdict = Field(
        default=Verdict.REVIEW,
        sa_column=enum_column(Verdict, length=10, default=Verdict.REVIEW),
    )
    review_status: str = Field(default="pending", max_length=30)
    reviewed_by: Optional[str] = Field(default=None, max_length=50)
    review_decision: Optional[str] = Field(default=None, max_length=30)
    proposal_id: Optional[str] = Field(
        default=None, foreign_key="proposals.id", max_length=26
    )


class VisualFinding(SQLModel, table=True):
    __tablename__ = "visual_findings"
    __table_args__ = (Index("idx_vf_result_id", "inspection_result_id"),)

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    inspection_result_id: str = Field(
        foreign_key="inspection_results.id", max_length=26, nullable=False
    )
    zone: str = Field(max_length=50, nullable=False)
    defect_type: str = Field(max_length=50, nullable=False)
    size_cm: Optional[float] = Field(default=None)
    confidence: float = Field(default=0.0)
    observation: Optional[str] = Field(default=None)
    media_id: Optional[str] = Field(
        default=None, foreign_key="media_artifacts.id", max_length=26
    )


class BuildsheetMismatch(SQLModel, table=True):
    __tablename__ = "buildsheet_mismatches"
    __table_args__ = (Index("idx_bsm_result_id", "inspection_result_id"),)

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    inspection_result_id: str = Field(
        foreign_key="inspection_results.id", max_length=26, nullable=False
    )
    attribute: str = Field(max_length=50, nullable=False)
    expected: str = Field(max_length=100, nullable=False)
    found: str = Field(max_length=100, nullable=False)
    confidence: float = Field(default=0.0)


class FunctionalStepCheck(SQLModel, table=True):
    __tablename__ = "functional_step_checks"
    __table_args__ = (Index("idx_fsc_result_id", "inspection_result_id"),)

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    inspection_result_id: str = Field(
        foreign_key="inspection_results.id", max_length=26, nullable=False
    )
    step_number: int = Field(nullable=False)
    name: str = Field(max_length=100, nullable=False)
    telemetry_ok: Optional[bool] = Field(default=None)
    camera_ok: Optional[bool] = Field(default=None)
    camera_confidence: Optional[float] = Field(default=None)
    observation: Optional[str] = Field(default=None)
    disagree: bool = Field(default=False)
    camera_applicable: bool = Field(default=True)
    media_id: Optional[str] = Field(
        default=None, foreign_key="media_artifacts.id", max_length=26
    )


class CarReport(TenantScopedModel, table=True):
    __tablename__ = "car_reports"
    __table_args__ = (UniqueConstraint("car_id", name="uq_reports_car_id"),)

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    car_id: str = Field(foreign_key="cars.id", max_length=26, nullable=False)
    verdict: Verdict = Field(
        default=Verdict.REVIEW,
        sa_column=enum_column(Verdict, length=10, default=Verdict.REVIEW),
    )
    summary: str = Field(default="", nullable=False)
    confirmed_by: Optional[str] = Field(default=None, max_length=50)
    confirmed_at: Optional[datetime] = Field(default=None)
    reinspection_status: str = Field(default="not_required", max_length=30)


class ReworkTicket(TenantScopedModel, table=True):
    __tablename__ = "rework_tickets"
    __table_args__ = (
        UniqueConstraint("ticket_id", name="uq_rework_ticket_id"),
        Index("idx_rework_car_id", "car_id"),
        Index("idx_rework_report_id", "report_id"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    ticket_id: str = Field(max_length=50, nullable=False)
    car_id: str = Field(foreign_key="cars.id", max_length=26, nullable=False)
    report_id: Optional[str] = Field(
        default=None, foreign_key="car_reports.id", max_length=26
    )
    bay_code: str = Field(max_length=50, nullable=False)
    defect_type: str = Field(max_length=50, nullable=False)
    status: str = Field(default="open", max_length=30)
    reason: str = Field(default="", nullable=False)
    confirmed_by: Optional[str] = Field(default=None, max_length=50)
    done_by: Optional[str] = Field(default=None, max_length=50)
    done_at: Optional[datetime] = Field(default=None)
