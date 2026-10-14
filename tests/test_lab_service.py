"""Sprint 4 gate: LabService scenarios, releases and MediaService."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import marshal_api.models  # noqa: F401  (registers tables)
from marshal_api.core.deps import get_current_tenant, get_job_runner, get_session
from marshal_api.main import app
from marshal_api.models import (
    MediaArtifact,
    Plant,
    Release as ReleaseRow,
    ReleaseEvalTest as EvalTestRow,
    Scenario as ScenarioRow,
    ScenarioStatus as DbScenarioStatus,
    Tenant,
    TestRun as TestRunRow,
)
from marshal_api.workers.job_runner import JobRunner

TENANT = "t1"


async def _seed(session: AsyncSession) -> None:
    session.add(Tenant(id=TENANT, name="Alpha", slug="alpha"))
    await session.flush()
    session.add(Plant(id="p1", tenant_id=TENANT, code="P1", name="Plant Alpha"))
    await session.flush()
    session.add_all(
        [
            ScenarioRow(id="sc1", tenant_id=TENANT, plant_id="p1", scenario_id="SC-1",
                        source="incident", status=DbScenarioStatus.VALID, summary="blocked lane",
                        tags=["from-incident"], spec={"id": "SC-1"}, validator_notes=["looks fine"]),
            ScenarioRow(id="sc2", tenant_id=TENANT, plant_id="p1", scenario_id="SC-2",
                        source="manual", status=DbScenarioStatus.IN_LIBRARY, summary="runnable",
                        tags=["regression"], spec={"id": "SC-2"}),
            ReleaseRow(id="rel1", tenant_id=TENANT, release_id="REL-1", name="agent-1.0",
                       evaluation_status="done", gate="open", passed_count=3, failed_count=1),
            ReleaseRow(id="rel2", tenant_id=TENANT, release_id="REL-2", name="agent-1.1",
                       evaluation_status="done", gate="open", passed_count=4, failed_count=0),
            MediaArtifact(id="m1", tenant_id=TENANT, plant_id="p1", media_id="MEDIA-1",
                          kind="clip", storage_uri="local/clip.mp4", content_type="video/mp4",
                          available=True),
            MediaArtifact(id="m2", tenant_id=TENANT, plant_id="p1", media_id="MEDIA-2",
                          kind="photo", storage_uri="local/gone.jpg", content_type="image/jpeg",
                          available=False),
        ]
    )
    await session.flush()
    session.add_all(
        [
            TestRunRow(id="run1", tenant_id=TENANT, scenario_id="sc2", run_id="RUN-1",
                       variant="SC-2-v1", params={}, metrics={"min_gap_m": 1.2}, passed=False,
                       failed_criteria=["min_gap_m"]),
            EvalTestRow(id="et1", release_id="rel1", test_id="T-1", metric="min_gap_m",
                        criteria=">= 2", result="1.2", passed=False),
        ]
    )
    await session.commit()


@pytest.fixture
async def api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lab.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await _seed(session)

    async def _session():
        async with maker() as session:
            yield session

    runner = JobRunner(session_factory=maker)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_tenant] = lambda: TENANT
    app.dependency_overrides[get_job_runner] = lambda: runner

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker, runner

    app.dependency_overrides.clear()
    await engine.dispose()


def _engineer(role: str = "engineer") -> dict[str, str]:
    return {"X-Actor-Role": role, "X-Tenant-ID": TENANT}


async def test_scenario_detail_projects_children(api) -> None:
    client, _, _ = api
    body = (await client.get("/api/v1/lab/scenarios/SC-2", headers={"X-Tenant-ID": TENANT})).json()
    assert body["status"] == "runnable" and body["tags"] == ["regression"]
    assert body["adversarial_result"]["verdict"] == "FRAGILE"
    assert body["adversarial_result"]["smallest_failing_variant"] == "SC-2-v1"

    pending = (await client.get("/api/v1/lab/scenarios", params={"status": "pending_review"}, headers={"X-Tenant-ID": TENANT})).json()
    assert [s["scenario_id"] for s in pending["items"]] == ["SC-1"]


async def test_scenario_review_gate(api) -> None:
    client, _, _ = api
    forbidden = await client.post(
        "/api/v1/lab/scenarios/SC-1/review",
        json={"decision": "approved", "actor_id": "ins-1"},
        headers=_engineer("inspector"),
    )
    assert forbidden.status_code == 403

    approved = await client.post(
        "/api/v1/lab/scenarios/SC-1/review",
        json={"decision": "approved", "actor_id": "eng-1"},
        headers=_engineer(),
    )
    assert approved.status_code == 200 and approved.json()["status"] == "runnable"

    again = await client.post(
        "/api/v1/lab/scenarios/SC-1/review",
        json={"decision": "rejected", "actor_id": "eng-1"},
        headers=_engineer(),
    )
    assert again.status_code == 409


async def test_release_gate_approval_criteria(api) -> None:
    client, _, _ = api
    blocked = await client.post(
        "/api/v1/lab/releases/REL-1/approve", json={"actor_id": "eng-1"}, headers=_engineer()
    )
    assert blocked.status_code == 409 and blocked.json()["code"] == "gate_blocked"

    ok = await client.post(
        "/api/v1/lab/releases/REL-2/approve", json={"actor_id": "eng-1"}, headers=_engineer()
    )
    assert ok.status_code == 200 and ok.json()["gate"] == "approved"

    detail = (await client.get("/api/v1/lab/releases/REL-1", headers={"X-Tenant-ID": TENANT})).json()
    assert detail["evaluation_status"] == "done" and detail["failed"] == 1
    assert detail["tests"][0]["test_id"] == "T-1"


async def test_adversarial_requires_runnable(api) -> None:
    client, _, _ = api
    not_runnable = await client.post(
        "/api/v1/lab/scenarios/SC-1/adversarial-search", headers=_engineer()
    )
    assert not_runnable.status_code == 409

    accepted = await client.post(
        "/api/v1/lab/scenarios/SC-2/adversarial-search", headers=_engineer()
    )
    assert accepted.status_code == 202 and accepted.json()["status"] == "queued"


async def test_create_scenario_job_creates_scenario(api) -> None:
    client, _, _ = api
    accepted = await client.post(
        "/api/v1/lab/scenarios",
        json={"situation": "forklift crosses the yard", "source": "manual"},
        headers=_engineer(),
    )
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]

    for _ in range(200):
        job = (await client.get(f"/api/v1/jobs/{job_id}", headers={"X-Tenant-ID": TENANT})).json()
        if job["status"] in ("done", "failed"):
            break
        await asyncio.sleep(0.01)
    assert job["status"] == "done" and job["result"]["degraded"] is True

    scenarios = (await client.get("/api/v1/lab/scenarios", headers={"X-Tenant-ID": TENANT})).json()
    created = [s for s in scenarios["items"] if s["summary"] == "forklift crosses the yard"]
    assert created and created[0]["status"] == "pending_review"


async def test_media_resolution(api) -> None:
    client, _, _ = api
    ok = (await client.get("/api/v1/media/MEDIA-1", headers={"X-Tenant-ID": TENANT})).json()
    assert ok["kind"] == "clip" and ok["available"] is True and ok["url"]

    unavailable = await client.get("/api/v1/media/MEDIA-2", headers={"X-Tenant-ID": TENANT})
    assert unavailable.status_code == 410

    missing = await client.get("/api/v1/media/NOPE", headers={"X-Tenant-ID": TENANT})
    assert missing.status_code == 404
