"""Supervisor proposal inbox and decision endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from marshal_api.core.deps import RequireRole, get_proposal_service
from marshal_api.core.idempotency import get_idempotency_key
from marshal_api.schemas import DecideProposalRequest, Page, Proposal
from marshal_api.services import ProposalService

router = APIRouter(tags=["proposals"])

_supervisor = RequireRole(["supervisor"])


@router.get("/proposals", response_model=Page[Proposal])
async def list_proposals(
    kind: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: ProposalService = Depends(get_proposal_service),
) -> Page[Proposal]:
    return await service.list_proposals(kind, status, page, limit)


@router.post(
    "/proposals/{proposal_id}/decide",
    response_model=dict,
    dependencies=[Depends(_supervisor), Depends(get_idempotency_key)],
)
async def decide_proposal(
    proposal_id: str,
    body: DecideProposalRequest,
    service: ProposalService = Depends(get_proposal_service),
) -> dict[str, Any]:
    return await service.decide_proposal(proposal_id, body.decision, body.actor_id, body.note)
