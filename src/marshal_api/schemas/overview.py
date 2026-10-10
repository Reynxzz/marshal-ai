"""Control Tower overview DTO."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from marshal_api.schemas.assistance import AssistanceCase
from marshal_api.schemas.proposals import Proposal
from marshal_api.schemas.yard import CarSummary


class OverviewCars(BaseModel):
    total: int = 0
    by_state: dict[str, int] = Field(default_factory=dict)
    exception: int = 0
    held: int = 0


class OverviewLab(BaseModel):
    scenarios_pending_review: int = 0
    releases_blocked: int = 0


class OverviewAttention(BaseModel):
    assistance: list[AssistanceCase] = Field(default_factory=list)
    proposals: list[Proposal] = Field(default_factory=list)


class Overview(BaseModel):
    plant: str
    updated_at: datetime
    cars: OverviewCars
    assistance_open: int = 0
    proposals_pending: int = 0
    lab: OverviewLab
    attention: OverviewAttention
    yard_exceptions: list[CarSummary] = Field(default_factory=list)
