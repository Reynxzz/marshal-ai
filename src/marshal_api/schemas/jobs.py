"""Async job and media DTOs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from marshal_api.schemas.common import JobStatus


class Job(BaseModel):
    job_id: str
    kind: str
    status: JobStatus
    progress: float | None = Field(default=None, ge=0, le=100)
    message: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class JobAccepted(BaseModel):
    job_id: str
    status: str = "queued"


class MediaKind(str, Enum):
    IMAGE = "image"
    CLIP = "clip"


class Media(BaseModel):
    media_id: str
    kind: MediaKind
    content_type: str
    url: str
    expires_at: datetime | None = None
    available: bool = True
