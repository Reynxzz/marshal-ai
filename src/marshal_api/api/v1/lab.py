"""Test lab: requests, scenarios, adversarial search and releases (Flows 6 & 7)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from marshal_api.core.deps import RequireRole, get_lab_service
from marshal_api.schemas import (
    CreateScenarioRequest,
    JobAccepted,
    Page,
    Release,
    ReviewScenarioRequest,
    Scenario,
    ScenarioDetail,
    TestRequest,
)
from marshal_api.services import LabService

router = APIRouter(tags=["lab"], prefix="/lab")

_engineer = RequireRole(["engineer"])


class ApproveReleaseRequest(BaseModel):
    actor_id: str


@router.get("/test-requests", response_model=Page[TestRequest])
async def list_test_requests(
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: LabService = Depends(get_lab_service),
) -> Page[TestRequest]:
    return await service.list_test_requests(status, page, limit)


@router.get("/scenarios", response_model=Page[Scenario])
async def list_scenarios(
    status: str | None = None,
    tag: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: LabService = Depends(get_lab_service),
) -> Page[Scenario]:
    return await service.list_scenarios(status, tag, page, limit)


@router.get("/scenarios/{scenario_id}", response_model=ScenarioDetail)
async def get_scenario(
    scenario_id: str, service: LabService = Depends(get_lab_service)
) -> ScenarioDetail:
    return await service.get_scenario(scenario_id)


@router.post(
    "/scenarios",
    response_model=JobAccepted,
    status_code=202,
    dependencies=[Depends(_engineer)],
)
async def create_scenario(
    body: CreateScenarioRequest, service: LabService = Depends(get_lab_service)
) -> JobAccepted:
    return await service.create_scenario(body.situation, body.source, body.request_id)


@router.post(
    "/scenarios/{scenario_id}/review",
    response_model=dict,
    dependencies=[Depends(_engineer)],
)
async def review_scenario(
    scenario_id: str,
    body: ReviewScenarioRequest,
    service: LabService = Depends(get_lab_service),
) -> dict[str, Any]:
    return await service.review_scenario(scenario_id, body.decision, body.actor_id, body.note)


@router.post(
    "/scenarios/{scenario_id}/adversarial-search",
    response_model=JobAccepted,
    status_code=202,
    dependencies=[Depends(_engineer)],
)
async def adversarial_search(
    scenario_id: str, service: LabService = Depends(get_lab_service)
) -> JobAccepted:
    return await service.start_adversarial(scenario_id)


@router.get("/releases", response_model=Page[Release])
async def list_releases(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: LabService = Depends(get_lab_service),
) -> Page[Release]:
    return await service.list_releases(page, limit)


@router.get("/releases/{release_id}", response_model=Release)
async def get_release(
    release_id: str, service: LabService = Depends(get_lab_service)
) -> Release:
    return await service.get_release(release_id)


@router.post(
    "/releases/{release_id}/evaluate",
    response_model=JobAccepted,
    status_code=202,
    dependencies=[Depends(_engineer)],
)
async def evaluate_release(
    release_id: str, service: LabService = Depends(get_lab_service)
) -> JobAccepted:
    return await service.evaluate_release(release_id)


@router.post(
    "/releases/{release_id}/approve",
    response_model=dict,
    dependencies=[Depends(_engineer)],
)
async def approve_release(
    release_id: str,
    body: ApproveReleaseRequest,
    service: LabService = Depends(get_lab_service),
) -> dict[str, Any]:
    return await service.approve_release(release_id, body.actor_id)
