"""Remote assistance cases, their ranked options, and supervisor proposals."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Index, UniqueConstraint, text
from sqlmodel import Field, Relationship, SQLModel

from marshal_api.db.session import DialectJSON
from marshal_api.models.base import (
    TenantScopedModel,
    enum_column,
    generate_ulid,
    utc_now,
)


class AssistanceStatus(str, Enum):
    OPEN = "OPEN"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AssistanceCase(TenantScopedModel, table=True):
    __tablename__ = "assistance_cases"
    __table_args__ = (
        UniqueConstraint("case_id", name="uq_cases_case_id"),
        Index("idx_cases_car_id", "car_id"),
        # Partial index: only active cases, supported by SQLite and PostgreSQL.
        Index(
            "idx_cases_open_partial",
            "tenant_id",
            "plant_id",
            postgresql_where=text("status != 'RESOLVED'"),
            sqlite_where=text("status != 'RESOLVED'"),
        ),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    case_id: str = Field(max_length=50, nullable=False)
    car_id: str = Field(foreign_key="cars.id", max_length=26, nullable=False)
    location: str = Field(max_length=50, nullable=False)
    situation: str = Field(default="", nullable=False)
    status: AssistanceStatus = Field(
        default=AssistanceStatus.OPEN,
        sa_column=enum_column(
            AssistanceStatus, length=20, default=AssistanceStatus.OPEN
        ),
    )
    moving_people_or_equipment: bool = Field(default=False)
    recommended_option_id: Optional[str] = Field(default=None, max_length=50)
    confidence: Optional[float] = Field(default=None)
    choice: Optional[str] = Field(default=None, max_length=50)
    resolved_by: Optional[str] = Field(default=None, max_length=50)
    clip_media_id: Optional[str] = Field(
        default=None, foreign_key="media_artifacts.id", max_length=26
    )
    opened_at: datetime = Field(default_factory=utc_now)
    resolved_at: Optional[datetime] = Field(default=None)

    car: Optional["Car"] = Relationship(back_populates="assistance_cases")
    options: List["AssistanceOption"] = Relationship(back_populates="case")


class AssistanceOption(SQLModel, table=True):
    __tablename__ = "assistance_options"
    __table_args__ = (Index("idx_options_case_id", "case_id"),)

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    case_id: str = Field(
        foreign_key="assistance_cases.id", max_length=26, nullable=False
    )
    option_id: str = Field(max_length=50, nullable=False)
    label: str = Field(max_length=100, nullable=False)
    risk: RiskLevel = Field(
        default=RiskLevel.LOW,
        sa_column=enum_column(RiskLevel, length=20, default=RiskLevel.LOW),
    )
    reason: Optional[str] = Field(default=None)
    rank: Optional[int] = Field(default=None)

    case: Optional[AssistanceCase] = Relationship(back_populates="options")


class Proposal(TenantScopedModel, table=True):
    __tablename__ = "proposals"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_proposals_proposal_id"),
        Index("idx_proposals_car_id", "car_id"),
        # Fast queue access: the supervisor's pending-approval inbox.
        Index("idx_proposals_tenant_status", "tenant_id", "status"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    proposal_id: str = Field(max_length=50, nullable=False)
    kind: str = Field(max_length=30, nullable=False)
    category: str = Field(max_length=50, nullable=False)
    car_id: Optional[str] = Field(
        default=None, foreign_key="cars.id", max_length=26
    )
    summary: str = Field(nullable=False)
    payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(DialectJSON))
    status: str = Field(default="pending", max_length=20)
    allowed_actions: List[str] = Field(
        default_factory=list, sa_column=Column(DialectJSON)
    )
    effects: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(DialectJSON))
    decision: Optional[str] = Field(default=None, max_length=50)
    decided_by: Optional[str] = Field(default=None, max_length=50)
    decided_at: Optional[datetime] = Field(default=None)
