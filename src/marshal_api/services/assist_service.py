"""Remote assistance decisions and escalation (Flow 2)."""

from __future__ import annotations

import logging

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import selectinload

from marshal_ai.driving import AssistChoose, MissionClient
from marshal_ai.events import EventBus

from marshal_api.core.errors import ApiError
from marshal_api.models import AssistanceCase as AssistanceCaseRow
from marshal_api.models import AssistanceStatus, CarState
from marshal_api.models.base import utc_now
from marshal_api.schemas import (
    AssistanceCase,
    AssistanceKpis,
    AssistanceOption,
    Page,
    Pagination,
)
from marshal_api.schemas.assistance import (
    AssistanceRecommendation,
    AssistanceStatus as CaseStatus,
    RiskLevel,
)

log = logging.getLogger("marshal.api.assistance")


class AssistanceService:
    def __init__(
        self,
        session: AsyncSession,
        mission_client: MissionClient | None = None,
        event_bus: EventBus | None = None,
        tenant_id: str = "tenant-demo",
    ) -> None:
        self.session = session
        self.mission_client = mission_client
        self.event_bus = event_bus
        self.tenant_id = tenant_id

    def _eager(self, stmt):
        return stmt.options(
            selectinload(AssistanceCaseRow.options), selectinload(AssistanceCaseRow.car)
        )

    async def _case_row(self, case_id: str) -> AssistanceCaseRow | None:
        stmt = self._eager(
            select(AssistanceCaseRow).where(
                AssistanceCaseRow.tenant_id == self.tenant_id,
                AssistanceCaseRow.case_id == case_id,
            )
        )
        return (await self.session.execute(stmt)).scalars().first()

    def _dto(self, case: AssistanceCaseRow) -> AssistanceCase:
        options = sorted(case.options or [], key=lambda o: (o.rank is None, o.rank or 0))
        recommendation = (
            AssistanceRecommendation(
                option_id=case.recommended_option_id, confidence=case.confidence or 0.0
            )
            if case.recommended_option_id
            else None
        )
        return AssistanceCase(
            case_id=case.case_id,
            vin=case.car.vin if case.car else "",
            location=case.location,
            situation=case.situation,
            status=CaseStatus(case.status),
            opened_at=case.opened_at,
            moving_people_or_equipment=case.moving_people_or_equipment,
            recommendation=recommendation,
            options=[
                AssistanceOption(
                    id=o.option_id,
                    label=o.label,
                    risk=RiskLevel(o.risk),
                    reason=o.reason,
                    rank=o.rank,
                )
                for o in options
            ],
            media_ids=[case.clip_media_id] if case.clip_media_id else [],
            resolved_by=case.resolved_by,
            resolved_at=case.resolved_at,
            chosen_option=case.choice,
        )

    async def list_cases(
        self,
        status: str | None = None,
        open_only: bool = False,
        page: int = 1,
        limit: int = 20,
    ) -> Page[AssistanceCase]:
        conditions = [AssistanceCaseRow.tenant_id == self.tenant_id]
        if status:
            conditions.append(AssistanceCaseRow.status == status)
        if open_only:
            conditions.append(AssistanceCaseRow.status != AssistanceStatus.RESOLVED)
        rows = list(
            (await self.session.execute(self._eager(select(AssistanceCaseRow).where(*conditions))))
            .scalars()
            .all()
        )
        # Open first, escalations before plain opens, then oldest waiting first.
        rows.sort(key=lambda c: (c.status == AssistanceStatus.RESOLVED, c.status != AssistanceStatus.ESCALATED, c.opened_at))
        total = len(rows)
        start = (page - 1) * limit
        return Page(
            items=[self._dto(c) for c in rows[start : start + limit]],
            pagination=Pagination(
                page=page, limit=limit, total=total, last_page=max(1, -(-total // limit))
            ),
        )

    async def get_case(self, case_id: str) -> AssistanceCase:
        case = await self._case_row(case_id)
        if case is None:
            raise ApiError(404, "case_not_found", f"Case {case_id} not found")
        return self._dto(case)

    async def get_kpis(self) -> AssistanceKpis:
        rows = (
            await self.session.execute(
                select(AssistanceCaseRow).where(AssistanceCaseRow.tenant_id == self.tenant_id)
            )
        ).scalars().all()
        resolved = [c for c in rows if c.status == AssistanceStatus.RESOLVED]
        latencies = [
            (c.resolved_at - c.opened_at).total_seconds() / 60
            for c in resolved
            if c.resolved_at
        ]
        average = sum(latencies) / len(latencies) if latencies else 0.0
        return AssistanceKpis(
            open=sum(1 for c in rows if c.status != AssistanceStatus.RESOLVED),
            resolved=len(resolved),
            avg_latency_minutes=round(average, 1),
            period="shift-ini",
        )

    async def decide_case(self, case_id: str, option_id: str, actor_id: str) -> dict:
        case = await self._case_row(case_id)
        if case is None:
            raise ApiError(404, "case_not_found", f"Case {case_id} not found")
        if case.status == AssistanceStatus.RESOLVED:
            raise ApiError(409, "case_already_resolved", "Case already resolved")

        options = case.options or []
        chosen = next((o for o in options if o.option_id == option_id), None)
        if chosen is None:
            raise ApiError(
                422, "option_not_allowed", f"Option '{option_id}' was not offered by the vehicle"
            )

        case.status = AssistanceStatus.RESOLVED
        case.choice = option_id
        case.resolved_by = actor_id
        case.resolved_at = utc_now()
        car = case.car
        if car is not None and car.state == CarState.EXCEPTION:
            car.state = CarState.WAITING_DISPATCH
            self.session.add(car)
        self.session.add(case)
        await self.session.commit()

        if self.mission_client is not None:
            try:
                await self.mission_client.send(
                    AssistChoose(
                        case_id=case.case_id,
                        vin=car.vin if car else "",
                        option=option_id,
                        chosen_by=actor_id,
                    )
                )
            except Exception:
                log.exception("assist dispatch failed for %s", case_id)

        return {
            "ok": True,
            "case_id": case.case_id,
            "status": "RESOLVED",
            "chosen_option": option_id,
            "resolved_by": actor_id,
            "resolved_at": case.resolved_at.isoformat(),
            "result": f"Decision '{chosen.label}' dispatched; the vehicle continues.",
        }
