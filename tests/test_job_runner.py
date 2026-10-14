"""Sprint 4 gate: async job runner lifecycle through GET /jobs/{id}."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import marshal_api.models  # noqa: F401  (registers tables)
from marshal_api.core.deps import get_current_tenant, get_session
from marshal_api.main import app
from marshal_api.models import AsyncJob, Plant, Tenant
from marshal_api.workers.job_runner import JobRunner

TENANT = "t1"


@pytest.fixture
async def api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        session.add(Tenant(id=TENANT, name="Alpha", slug="alpha"))
        await session.flush()
        session.add(Plant(id="p1", tenant_id=TENANT, code="P1", name="Plant Alpha"))
        await session.commit()

    async def _session():
        async with maker() as session:
            yield session

    runner = JobRunner(session_factory=maker)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_tenant] = lambda: TENANT

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker, runner

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_async_job_runner_lifecycle(api) -> None:
    client, maker, runner = api
    job_id = runner.new_job_id()
    async with maker() as session:
        session.add(
            AsyncJob(tenant_id=TENANT, plant_id="p1", job_id=job_id, kind="dummy",
                     status="queued", progress=0)
        )
        await session.commit()

    seen: set[str] = set()

    async def handler(job_id, payload, runner):
        await runner.set_progress(job_id, 40, "step 1")
        await asyncio.sleep(0.01)
        await runner.set_progress(job_id, 80, "step 2")
        return {"ok": True}

    runner.enqueue(job_id, handler, {})
    # first observation may already be running; poll until terminal
    for _ in range(200):
        body = (await client.get(f"/api/v1/jobs/{job_id}", headers={"X-Tenant-ID": TENANT})).json()
        seen.add(body["status"])
        if body["status"] in ("done", "failed"):
            break
        await asyncio.sleep(0.01)

    assert body["status"] == "done"
    assert body["progress"] == 100 and body["result"] == {"ok": True}
    assert "running" in seen and seen <= {"queued", "running", "done"}


async def test_job_not_found(api) -> None:
    client, _, _ = api
    response = await client.get("/api/v1/jobs/JOB-NOPE", headers={"X-Tenant-ID": TENANT})
    assert response.status_code == 404 and response.json()["code"] == "job_not_found"
