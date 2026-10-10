"""The test lab: scenarios, runs, releases and their gating tests."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from marshal_api.db.session import DialectJSON
from marshal_api.models.base import (
    TenantScopedModel,
    enum_column,
    generate_ulid,
    utc_now,
)


class ScenarioStatus(str, Enum):
    DRAFT = "draft"
    VALID = "valid"
    DRY_RUN_OK = "dry_run_ok"
    IN_LIBRARY = "in_library"
    REJECTED = "rejected"


class TestRequest(TenantScopedModel, table=True):
    __tablename__ = "test_requests"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_test_requests_request_id"),
        Index("idx_test_requests_tenant_status", "tenant_id", "status"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    request_id: str = Field(max_length=50, nullable=False)
    source: str = Field(max_length=50, nullable=False)
    text: str = Field(default="", nullable=False)
    status: str = Field(default="new", max_length=20)
    scenario_id: Optional[str] = Field(
        default=None, foreign_key="scenarios.id", max_length=26
    )


class Scenario(TenantScopedModel, table=True):
    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("scenario_id", name="uq_scenarios_scenario_id"),
        Index("idx_scenarios_tenant_status", "tenant_id", "status"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    scenario_id: str = Field(max_length=50, nullable=False)
    source: str = Field(max_length=50, nullable=False)
    status: ScenarioStatus = Field(
        default=ScenarioStatus.DRAFT,
        sa_column=enum_column(
            ScenarioStatus, length=20, default=ScenarioStatus.DRAFT
        ),
    )
    tags: List[str] = Field(default_factory=list, sa_column=Column(DialectJSON))
    owner: Optional[str] = Field(default=None, max_length=50)
    summary: str = Field(default="", nullable=False)
    spec: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(DialectJSON))
    validator_notes: List[str] = Field(
        default_factory=list, sa_column=Column(DialectJSON)
    )
    dry_run_metrics: Dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(DialectJSON)
    )


class TestRun(SQLModel, table=True):
    __test__ = False  # not a pytest class

    __tablename__ = "test_runs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_test_runs_run_id"),
        Index("idx_runs_scenario_id", "scenario_id"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    tenant_id: str = Field(foreign_key="tenants.id", max_length=26, nullable=False)
    scenario_id: str = Field(
        foreign_key="scenarios.id", max_length=26, nullable=False
    )
    run_id: str = Field(max_length=50, nullable=False)
    variant: str = Field(max_length=100, nullable=False)
    params: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(DialectJSON))
    metrics: Dict[str, float] = Field(
        default_factory=dict, sa_column=Column(DialectJSON)
    )
    passed: bool = Field(default=False)
    failed_criteria: List[str] = Field(
        default_factory=list, sa_column=Column(DialectJSON)
    )
    clip_media_id: Optional[str] = Field(
        default=None, foreign_key="media_artifacts.id", max_length=26
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False)


class Release(SQLModel, table=True):
    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("release_id", name="uq_releases_release_id"),)

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    tenant_id: str = Field(foreign_key="tenants.id", max_length=26, nullable=False)
    release_id: str = Field(max_length=50, nullable=False)
    name: str = Field(max_length=100, nullable=False)
    evaluation_status: str = Field(default="pending", max_length=30)
    gate: str = Field(default="open", max_length=30)
    passed_count: int = Field(default=0)
    failed_count: int = Field(default=0)
    approved_by: Optional[str] = Field(default=None, max_length=50)
    approved_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        sa_column_kwargs={"onupdate": utc_now},
    )


class ReleaseEvalTest(SQLModel, table=True):
    __tablename__ = "release_eval_tests"
    __table_args__ = (Index("idx_ret_release_id", "release_id"),)

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    release_id: str = Field(
        foreign_key="releases.id", max_length=26, nullable=False
    )
    scenario_id: Optional[str] = Field(
        default=None, foreign_key="scenarios.id", max_length=26
    )
    test_run_id: Optional[str] = Field(
        default=None, foreign_key="test_runs.id", max_length=26
    )
    test_id: str = Field(max_length=50, nullable=False)
    metric: str = Field(max_length=50, nullable=False)
    criteria: str = Field(max_length=100, nullable=False)
    result: str = Field(max_length=50, nullable=False)
    passed: bool = Field(default=False)
    clip_media_id: Optional[str] = Field(
        default=None, foreign_key="media_artifacts.id", max_length=26
    )
