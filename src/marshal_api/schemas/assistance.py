"""Remote assistance DTOs (Flow 2)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AssistanceStatus(str, Enum):
    OPEN = "OPEN"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AssistanceOption(BaseModel):
    id: str
    label: str
    risk: RiskLevel
    reason: str | None = None
    rank: int | None = None


class AssistanceRecommendation(BaseModel):
    option_id: str
    confidence: float
    note: str | None = None


class AssistanceCase(BaseModel):
    case_id: str
    vin: str
    location: str
    situation: str
    status: AssistanceStatus
    opened_at: datetime
    escalation: str | None = None
    moving_people_or_equipment: bool = False
    risk_note: str | None = None
    recommendation: AssistanceRecommendation | None = None
    options: list[AssistanceOption] = Field(default_factory=list)
    media_ids: list[str] = Field(default_factory=list)
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    chosen_option: str | None = None
    result: str | None = None


class AssistanceKpis(BaseModel):
    open: int = 0
    resolved: int = 0
    avg_latency_minutes: float = 0.0
    period: str | None = None


class DecideAssistanceRequest(BaseModel):
    option: str
    actor_id: str
