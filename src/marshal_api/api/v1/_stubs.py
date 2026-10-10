"""Placeholder responses for the Phase 2 scaffolding.

Every builder returns an object that satisfies the frontend contract so the
routes, response models and DI plumbing can be verified before any business
logic exists. Phase 3+ replaces each builder with real queries.
"""

from __future__ import annotations

from datetime import datetime, timezone

from marshal_api.schemas import (
    Actor,
    AssistanceCase,
    AssistanceKpis,
    CarDetail,
    FunctionalInspection,
    Health,
    Job,
    KpiResponse,
    Media,
    Overview,
    Readiness,
    Report,
    Role,
    ScenarioDetail,
    Release,
    VisualInspection,
    Yard,
)
from marshal_api.schemas.common import (
    Dependency,
    DependencyStatus,
    HealthStatus,
    JobStatus,
    ReadinessStatus,
    ReviewStatus,
    Verdict,
)
from marshal_api.schemas.overview import OverviewAttention, OverviewCars, OverviewLab
from marshal_api.schemas.yard import BuildSheet, ReworkBayList, YardSummary, YardMap

PLANT = "Plant 01"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def pagination(page: int = 1, limit: int = 20, total: int = 0) -> dict:
    last_page = max(1, -(-total // limit)) if limit else 1
    return {"page": page, "limit": limit, "total": total, "last_page": last_page}


def page(items: list | None = None, page: int = 1, limit: int = 20) -> dict:
    items = items or []
    return {"items": items, "pagination": pagination(page, limit, len(items))}


def health() -> Health:
    return Health(status=HealthStatus.OK, uptime_s=0.0)


def readiness() -> Readiness:
    return Readiness(
        status=ReadinessStatus.READY,
        dependencies=[
            Dependency(name="database", status=DependencyStatus.OK),
            Dependency(name="event_bus", status=DependencyStatus.OK),
            Dependency(name="carla", status=DependencyStatus.OK),
        ],
    )


def actor(role: Role = Role.SUPERVISOR, actor_id: str = "actor-demo") -> Actor:
    return Actor(
        actor_id=actor_id,
        display_name=role.value.title(),
        role=role,
        plant=PLANT,
        permissions=[],
    )


def overview() -> Overview:
    return Overview(
        plant=PLANT,
        updated_at=_now(),
        cars=OverviewCars(),
        assistance_open=0,
        proposals_pending=0,
        lab=OverviewLab(),
        attention=OverviewAttention(),
        yard_exceptions=[],
    )


def kpis() -> KpiResponse:
    return KpiResponse(items=[])


def yard() -> Yard:
    return Yard(
        plant=PLANT,
        updated_at=_now(),
        summary=YardSummary(),
        cars=[],
        trucks=[],
        zones=[],
        bays=[],
    )


def yard_map() -> YardMap:
    return YardMap(points=[])


def rework_bays() -> ReworkBayList:
    return ReworkBayList(items=[])


def car_detail(vin: str) -> CarDetail:
    return CarDetail(
        vin=vin,
        state="exception",
        location="line_end",
        updated_at=_now(),
        build_sheet=BuildSheet(model="UNKNOWN", color="UNKNOWN"),
        created_at=_now(),
    )


def assistance_case(case_id: str) -> AssistanceCase:
    return AssistanceCase(
        case_id=case_id,
        vin="UNKNOWN",
        location="UNKNOWN",
        situation="",
        status="OPEN",
        opened_at=_now(),
    )


def assistance_kpis() -> AssistanceKpis:
    return AssistanceKpis()


def visual_inspection(vin: str) -> VisualInspection:
    return VisualInspection(
        result_id=f"IR-{vin}-visual",
        vin=vin,
        verdict=Verdict.REVIEW,
        at=_now(),
        review_status=ReviewStatus.PENDING,
    )


def functional_inspection(vin: str) -> FunctionalInspection:
    return FunctionalInspection(
        result_id=f"IR-{vin}-functional",
        vin=vin,
        verdict=Verdict.REVIEW,
        at=_now(),
        review_status=ReviewStatus.PENDING,
    )


def report(vin: str) -> Report:
    return Report(vin=vin, verdict=Verdict.REVIEW, summary="", updated_at=_now())


def scenario_detail(scenario_id: str) -> ScenarioDetail:
    return ScenarioDetail(
        scenario_id=scenario_id,
        source="manual",
        status="draft",
        owner="",
        summary="",
        created_at=_now(),
    )


def release(release_id: str) -> Release:
    return Release(
        release_id=release_id,
        evaluation_status="none",
        gate="pending",
        updated_at=_now(),
    )


def job(job_id: str) -> Job:
    return Job(
        job_id=job_id,
        kind="unknown",
        status=JobStatus.QUEUED,
        progress=0,
        created_at=_now(),
        updated_at=_now(),
    )


def media(media_id: str) -> Media:
    return Media(
        media_id=media_id,
        kind="image",
        content_type="application/octet-stream",
        url="",
        available=False,
    )
