"""Supervisor proposal DTOs (human authority gate)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProposalKind(str, Enum):
    REPLAN = "replan"
    INSPECTION_REVIEW = "inspection_review"
    REWORK = "rework"


class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class ProposalEffects(BaseModel):
    approve: str | None = None
    reject: str | None = None


class Proposal(BaseModel):
    proposal_id: str
    kind: ProposalKind
    vin: str | None = None
    category: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: ProposalStatus
    allowed_actions: list[str] = Field(default_factory=list)
    effects: ProposalEffects | None = None
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision: str | None = None


class DecideProposalRequest(BaseModel):
    decision: str
    actor_id: str
    note: str | None = None
