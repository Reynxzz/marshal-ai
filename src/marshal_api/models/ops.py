"""Background jobs, media artifacts and the plant event stream."""

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import BigInteger, Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from marshal_api.db.session import DialectJSON
from marshal_api.models.base import TenantScopedModel, generate_ulid, utc_now


class AsyncJob(TenantScopedModel, table=True):
    __tablename__ = "async_jobs"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_jobs_job_id"),
        # Prevents table scans during frontend job-status polling.
        Index("idx_jobs_tenant_status", "tenant_id", "status"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    job_id: str = Field(max_length=50, nullable=False)
    kind: str = Field(max_length=50, nullable=False)
    status: str = Field(default="queued", max_length=20)
    progress: int = Field(default=0)
    message: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)
    result: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(DialectJSON))


class MediaArtifact(TenantScopedModel, table=True):
    __tablename__ = "media_artifacts"
    __table_args__ = (UniqueConstraint("media_id", name="uq_media_media_id"),)

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    media_id: str = Field(max_length=50, nullable=False)
    kind: str = Field(max_length=30, nullable=False)  # photo | clip | replay
    storage_uri: str = Field(max_length=500, nullable=False)
    content_type: str = Field(default="application/octet-stream", max_length=100)
    file_size_bytes: int = Field(default=0)
    available: bool = Field(default=True)


class EventLog(TenantScopedModel, table=True):
    __tablename__ = "event_logs"
    __table_args__ = (
        # Powers the /events cursor stream (since=seq).
        Index("idx_events_stream", "tenant_id", "plant_id", "seq"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    seq: int = Field(sa_column=Column(BigInteger, nullable=False))
    flow_number: Optional[int] = Field(default=None)
    source: str = Field(max_length=50, nullable=False)
    title: str = Field(max_length=200, nullable=False)
    detail: str = Field(default="", nullable=False)
    level: str = Field(default="info", max_length=20)
    payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(DialectJSON))
    recorded_at: datetime = Field(default_factory=utc_now, nullable=False)
