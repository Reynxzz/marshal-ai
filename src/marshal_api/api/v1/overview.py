"""Control Tower overview and KPI endpoints (live DB aggregations)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from marshal_ai.store import Store

from marshal_api.config import Settings, get_settings
from marshal_api.core.deps import (
    get_assistance_service,
    get_current_tenant,
    get_proposal_service,
    get_session,
    get_store,
    get_yard_service,
)
from marshal_api.live import LiveOverviewService
from marshal_api.models import InspectionResult, Release, Scenario
from marshal_api.models.base import utc_now
from marshal_api.models.lab import ScenarioStatus
from marshal_api.schemas import KpiResponse, Overview
from marshal_api.schemas.common import Kpi, KpiTone, Verdict
from marshal_api.schemas.overview import OverviewAttention, OverviewCars, OverviewLab
from marshal_api.services import AssistanceService, ProposalService, YardService

router = APIRouter(tags=["overview"])

ATTENTION_LIMIT = 5


async def _lab_counts(session: AsyncSession, tenant_id: str) -> tuple[int, int]:
    pending = (
        await session.execute(
            select(func.count())
            .select_from(Scenario)
            .where(
                Scenario.tenant_id == tenant_id, Scenario.status == ScenarioStatus.VALID
            )
        )
    ).scalar_one()
    blocked = (
        await session.execute(
            select(func.count())
            .select_from(Release)
            .where(Release.tenant_id == tenant_id, Release.gate == "blocked")
        )
    ).scalar_one()
    return pending, blocked


async def _inspection_yield(session: AsyncSession, tenant_id: str) -> tuple[int, int]:
    rows = (
        await session.execute(
            select(InspectionResult.verdict, func.count())
            .where(InspectionResult.tenant_id == tenant_id)
            .group_by(InspectionResult.verdict)
        )
    ).all()
    counts = {verdict: count for verdict, count in rows}
    total = sum(counts.values())
    if not total:
        return 0, 0
    return round(counts.get(Verdict.PASS, 0) / total * 100), total


@router.get("/overview", response_model=Overview)
async def overview(
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    yard: YardService = Depends(get_yard_service),
    assistance: AssistanceService = Depends(get_assistance_service),
    proposals: ProposalService = Depends(get_proposal_service),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_current_tenant),
) -> Overview:
    if settings.store == "gcp":
        return await LiveOverviewService(store).overview()
    snapshot = await yard.get_yard_snapshot()
    open_cases = await assistance.list_cases(open_only=True, limit=ATTENTION_LIMIT)
    pending = await proposals.list_proposals(status="pending", limit=ATTENTION_LIMIT)
    scenarios_pending, releases_blocked = await _lab_counts(session, tenant_id)

    by_state: dict[str, int] = {}
    for car in snapshot.cars:
        key = car.state.value if hasattr(car.state, "value") else str(car.state)
        by_state[key] = by_state.get(key, 0) + 1

    exceptions = [
        car for car in snapshot.cars if car.state.value in ("exception", "held")
    ]
    return Overview(
        plant=snapshot.plant,
        updated_at=utc_now(),
        cars=OverviewCars(
            total=len(snapshot.cars),
            by_state=by_state,
            exception=by_state.get("exception", 0),
            held=by_state.get("held", 0),
        ),
        assistance_open=open_cases.pagination.total,
        proposals_pending=pending.pagination.total,
        lab=OverviewLab(
            scenarios_pending_review=scenarios_pending,
            releases_blocked=releases_blocked,
        ),
        attention=OverviewAttention(
            assistance=open_cases.items, proposals=pending.items
        ),
        yard_exceptions=exceptions,
    )


@router.get("/kpis", response_model=KpiResponse)
async def kpis(
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    yard: YardService = Depends(get_yard_service),
    assistance: AssistanceService = Depends(get_assistance_service),
    proposals: ProposalService = Depends(get_proposal_service),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_current_tenant),
) -> KpiResponse:
    if settings.store == "gcp":
        return await LiveOverviewService(store).kpis()
    snapshot = await yard.get_yard_snapshot()
    open_cases = await assistance.list_cases(open_only=True, limit=1)
    pending = await proposals.list_proposals(status="pending", limit=1)
    _, releases_blocked = await _lab_counts(session, tenant_id)
    first_pass, total_inspections = await _inspection_yield(session, tenant_id)

    by_state: dict[str, int] = {}
    for car in snapshot.cars:
        key = car.state.value if hasattr(car.state, "value") else str(car.state)
        by_state[key] = by_state.get(key, 0) + 1

    active = by_state.get("driving", 0) + by_state.get("waiting_dispatch", 0)
    exception = by_state.get("exception", 0) + by_state.get("held", 0)
    return KpiResponse(
        items=[
            Kpi(
                key="active",
                label="Active Car",
                value=active,
                unit="unit",
                tone=KpiTone.INFO,
            ),
            Kpi(
                key="assistance_open",
                label="Assistance Open",
                value=open_cases.pagination.total,
                unit="case",
                tone=KpiTone.DANGER if open_cases.pagination.total else KpiTone.SUCCESS,
            ),
            Kpi(
                key="proposals_pending",
                label="Proposal Pending",
                value=pending.pagination.total,
                unit="item",
                tone=KpiTone.WARNING if pending.pagination.total else KpiTone.SUCCESS,
            ),
            Kpi(
                key="exception",
                label="Exception / Held",
                value=exception,
                unit="unit",
                tone=KpiTone.DANGER
                if by_state.get("exception", 0)
                else KpiTone.DEFAULT,
            ),
            Kpi(
                key="first_pass",
                label="First-pass Yield",
                value=first_pass,
                unit="%",
                tone=(
                    KpiTone.DEFAULT
                    if total_inspections == 0
                    else KpiTone.SUCCESS
                    if first_pass >= 80
                    else KpiTone.WARNING
                    if first_pass >= 60
                    else KpiTone.DANGER
                ),
            ),
            Kpi(
                key="releases_blocked",
                label="Release Blocked",
                value=releases_blocked,
                unit="release",
                tone=KpiTone.DANGER if releases_blocked else KpiTone.SUCCESS,
            ),
        ]
    )
