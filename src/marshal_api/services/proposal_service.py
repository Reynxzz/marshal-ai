"""Human-authority proposal execution (Flows 1, 3, 5)."""

from __future__ import annotations

import logging

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from marshal_ai.events import EventBus, Topic

from marshal_api.core.errors import ApiError
from marshal_api.models import (
    Car,
    InspectionResult,
    Proposal as ProposalRow,
    ReworkTicket,
)
from marshal_api.models.base import utc_now
from marshal_api.schemas import Page, Pagination, Proposal
from marshal_api.schemas.proposals import ProposalEffects, ProposalKind, ProposalStatus

log = logging.getLogger("marshal.api.proposals")

# Decision alias -> stored proposal status. Mirrors marshal-fe mock/handlers/proposals.ts.
_DECISION_STATUS = {
    "approve": "approved",
    "confirm": "approved",
    "reject": "rejected",
    "escalate": "escalated",
}


class ProposalService:
    def __init__(
        self,
        session: AsyncSession,
        event_bus: EventBus | None = None,
        tenant_id: str = "tenant-demo",
    ) -> None:
        self.session = session
        self.event_bus = event_bus
        self.tenant_id = tenant_id

    async def _all(self, stmt):
        return (await self.session.execute(stmt)).scalars().all()

    async def _first(self, stmt):
        return (await self.session.execute(stmt)).scalars().first()

    async def _vins(self, car_ids: set[str]) -> dict[str, str]:
        if not car_ids:
            return {}
        rows = (await self.session.execute(select(Car.id, Car.vin).where(Car.id.in_(car_ids)))).all()
        return {car_id: vin for car_id, vin in rows}

    def _dto(self, p: ProposalRow, vins: dict[str, str]) -> Proposal:
        effects = p.effects or {}
        return Proposal(
            proposal_id=p.proposal_id,
            kind=ProposalKind(p.kind),
            vin=vins.get(p.car_id) if p.car_id else None,
            category=p.category,
            summary=p.summary,
            payload=p.payload or {},
            status=ProposalStatus(p.status),
            allowed_actions=list(p.allowed_actions or []),
            effects=(
                ProposalEffects(approve=effects.get("approve"), reject=effects.get("reject"))
                if effects
                else None
            ),
            created_at=p.created_at,
            decided_at=p.decided_at,
            decided_by=p.decided_by,
            decision=p.decision,
        )

    async def list_proposals(
        self, kind: str | None = None, status: str | None = None, page: int = 1, limit: int = 20
    ) -> Page[Proposal]:
        conditions = [ProposalRow.tenant_id == self.tenant_id]
        if kind:
            conditions.append(ProposalRow.kind == kind)
        if status:
            conditions.append(ProposalRow.status == status)

        total = (
            await self.session.execute(
                select(func.count()).select_from(ProposalRow).where(*conditions)
            )
        ).scalar_one()
        rows = await self._all(
            select(ProposalRow)
            .where(*conditions)
            .order_by(ProposalRow.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        vins = await self._vins({p.car_id for p in rows if p.car_id})
        return Page(
            items=[self._dto(p, vins) for p in rows],
            pagination=Pagination(
                page=page, limit=limit, total=total, last_page=max(1, -(-total // limit))
            ),
        )

    async def decide_proposal(
        self, proposal_id: str, decision: str, actor_id: str, note: str | None = None
    ) -> dict:
        proposal = await self._first(
            select(ProposalRow).where(
                ProposalRow.tenant_id == self.tenant_id,
                (ProposalRow.proposal_id == proposal_id) | (ProposalRow.id == proposal_id),
            )
        )
        if proposal is None:
            raise ApiError(404, "proposal_not_found", f"Proposal {proposal_id} not found")
        if proposal.status != "pending":
            raise ApiError(409, "proposal_already_decided", "Proposal already decided")

        normalized = _DECISION_STATUS.get(decision)
        if normalized is None:
            raise ApiError(422, "validation_error", f"Unknown decision '{decision}'")
        actions = list(proposal.allowed_actions or [])
        allowed = decision in actions or (
            decision in ("approve", "reject") and "confirm" in actions
        )
        if not allowed:
            raise ApiError(
                422, "action_not_allowed", f"Action '{decision}' not allowed for this proposal"
            )

        proposal.status = normalized
        proposal.decision = decision
        proposal.decided_by = actor_id
        proposal.decided_at = utc_now()
        await self._apply_effects(proposal, actor_id)
        self.session.add(proposal)
        await self.session.commit()

        if self.event_bus is not None:
            await self.event_bus.publish(
                Topic.VEHICLE_STATUS,
                {
                    "type": "proposal.decided",
                    "proposal_id": proposal.proposal_id,
                    "decision": decision,
                    "actor_id": actor_id,
                    "note": note,
                },
            )
        return {
            "ok": True,
            "proposal_id": proposal.proposal_id,
            "status": proposal.status,
            "decision": decision,
            "decided_by": actor_id,
            "decided_at": proposal.decided_at.isoformat(),
        }

    async def _apply_effects(self, proposal: ProposalRow, actor_id: str) -> None:
        """The side effects a decision triggers; only what the schema can hold."""
        payload = proposal.payload or {}
        if proposal.kind == "rework" and proposal.decision != "reject":
            ticket_id = payload.get("ticket_id")
            if ticket_id:
                ticket = await self._first(
                    select(ReworkTicket).where(ReworkTicket.ticket_id == ticket_id)
                )
                if ticket is not None and ticket.status == "open":
                    ticket.status = "in_repair"
                    ticket.confirmed_by = actor_id
                    self.session.add(ticket)
        elif proposal.kind == "inspection_review" and proposal.decision == "confirm":
            result_id = payload.get("result_id")
            if result_id:
                result = await self._first(
                    select(InspectionResult).where(InspectionResult.result_id == result_id)
                )
                if result is not None:
                    result.review_status = "done"
                    result.reviewed_by = actor_id
                    result.review_decision = "confirm"
                    self.session.add(result)
        # replan: the plan's mission dispatch is not part of the dashboard's decision contract
        # (see marshal-fe mock/handlers/proposals.ts), so nothing is dispatched here.
