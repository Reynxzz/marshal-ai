from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from marshal_api.core.deps import get_batch_service, get_current_tenant
from marshal_api.main import app
from marshal_api.services.batch_service import BatchService
from marshal_api.services.batch_store import MemoryBatchStore

SETTINGS = SimpleNamespace(batch_enabled=True, batch_max_cars=6, batch_timeout_s=30,
                           batch_lab_timeout_s=2, batch_lab_adversarial=4, model="test",
                           batch_plant_script="scripts/run_carla_plant.sh",
                           batch_lab_script="scripts/run_carla_lab.sh")


class ApiRunner:
    def __init__(self):
        self.started = asyncio.Event()
        self.stop = asyncio.Event()
        self.terminated = False

    async def stream(self, request):
        self.started.set()
        yield {"type": "plant.start", "cars": request.vins}
        yield {"type": "batch.event", "flow": 1, "source": "Yard", "title": "Released",
               "detail": "sample car", "level": "info"}
        await self.stop.wait()

    async def stream_lab(self, request):
        if False:
            yield {}

    async def terminate(self):
        self.terminated = True
        self.stop.set()


@pytest.fixture
async def api():
    from fastapi import Depends

    runner = ApiRunner()
    store = MemoryBatchStore()

    def service_dep(tenant_id: str = Depends(get_current_tenant)) -> BatchService:
        return BatchService(store, runner, tenant_id, settings=SETTINGS, llm_mode="offline")

    app.dependency_overrides[get_batch_service] = service_dep
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, runner
    app.dependency_overrides.clear()


def _headers(tenant="t1", role="supervisor"):
    return {"X-Tenant-ID": tenant, "X-Actor-Role": role}


async def test_batch_api_lifecycle_and_tenant_isolation(api) -> None:
    client, runner = api
    invalid = await client.post("/api/v1/batches", json={"cars": 0}, headers=_headers())
    assert invalid.status_code == 422
    forbidden = await client.post("/api/v1/batches", json={"cars": 1},
                                  headers=_headers(role="inspector"))
    assert forbidden.status_code == 403

    created = await client.post("/api/v1/batches", json={"cars": 1}, headers=_headers())
    assert created.status_code == 202
    batch_id = created.json()["batch_id"]
    await asyncio.wait_for(runner.started.wait(), timeout=2)

    detail = await client.get(f"/api/v1/batches/{batch_id}", headers=_headers())
    assert detail.status_code == 200 and detail.json()["status"] == "running"
    assert len(detail.json()["flow_counts"]) == 7
    assert detail.json()["flow_counts"]["flow3"]["pass"] == 0

    await asyncio.sleep(1.1)  # event rows are buffered and flushed at one-second intervals
    events = await client.get(f"/api/v1/batches/{batch_id}/events?since=0", headers=_headers())
    assert events.status_code == 200 and events.json()["items"][0]["title"] == "Released"

    second = await client.post("/api/v1/batches", json={"cars": 1}, headers=_headers())
    assert second.status_code == 409 and second.json()["code"] == "batch_running"
    report = await client.get(f"/api/v1/batches/{batch_id}/report", headers=_headers())
    assert report.status_code == 409

    cancelled = await client.post(f"/api/v1/batches/{batch_id}/cancel", headers=_headers())
    assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
    hidden = await client.get(f"/api/v1/batches/{batch_id}", headers=_headers(tenant="t2"))
    assert hidden.status_code == 404
    hidden_events = await client.get(f"/api/v1/batches/{batch_id}/events", headers=_headers(tenant="t2"))
    assert hidden_events.status_code == 404
    assert runner.terminated
