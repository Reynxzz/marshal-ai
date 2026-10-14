"""Sprint 5 gate: SSE backfill, camera proxy and health probes."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import marshal_api.models  # noqa: F401  (registers tables)
from marshal_api.core.deps import get_current_tenant, get_session
from marshal_api.main import app
from marshal_api.models import EventLog, Plant, Tenant
from marshal_api.schemas.common import Severity
from marshal_api.streaming.camera_proxy import STANDBY_JPEG, _standby_frames
from marshal_api.streaming.sse import _backfill, _live, _to_flow

TENANT = "t1"


async def _seed(session: AsyncSession) -> None:
    session.add(Tenant(id=TENANT, name="Alpha", slug="alpha"))
    await session.flush()
    session.add(Plant(id="p1", tenant_id=TENANT, code="P1", name="Plant Alpha"))
    await session.flush()
    session.add_all(
        [
            EventLog(id="e1", tenant_id=TENANT, plant_id="p1", seq=1, flow_number=1,
                     source="yard", title="released", detail="VIN-1", level="good"),
            EventLog(id="e2", tenant_id=TENANT, plant_id="p1", seq=2, flow_number=2,
                     source="assist", title="stopped", detail="box", level="warn"),
            EventLog(id="e3", tenant_id=TENANT, plant_id="p1", seq=3, flow_number=5,
                     source="rework", title="done", detail="VIN-1", level="bogus"),
        ]
    )
    await session.commit()


@pytest.fixture
async def api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stream.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await _seed(session)

    async def _session():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_tenant] = lambda: TENANT

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_backfill_maps_and_filters_by_cursor(api) -> None:
    _, maker = api
    async with maker() as session:
        all_events = await _backfill(session, TENANT, since=0)
        assert [e.seq for e in all_events] == [1, 2, 3]
        assert all_events[0].level is Severity.GOOD
        assert all_events[1].source == "assist"
        assert all_events[2].level is Severity.INFO  # unknown level falls back

        after = await _backfill(session, TENANT, since=2)
        assert [e.seq for e in after] == [3]


def test_live_event_from_bus_payload() -> None:
    event = _live({"type": "late_truck", "flow": 2, "source": "yard", "level": "warn"}, seq=9)
    assert event.seq == 9 and event.flow == 2 and event.level is Severity.WARN
    assert event.title == "late_truck"


def test_flow_event_row_mapping(api) -> None:
    # _to_flow is pure; build a detached row object
    row = EventLog(id="x", tenant_id=TENANT, plant_id="p1", seq=7, flow_number=3,
                   source="lab", title="t", detail="d", level="bad")
    event = _to_flow(row)
    assert event.seq == 7 and event.flow == 3 and event.level is Severity.BAD


async def test_camera_snapshot_returns_jpeg(api) -> None:
    client, _ = api
    assert STANDBY_JPEG[:3] == b"\xff\xd8\xff"
    response = await client.get("/api/v1/camera/snapshot")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:3] == b"\xff\xd8\xff"


async def test_standby_frame_shape() -> None:
    frame = await _standby_frames().__anext__()
    assert frame.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
    assert STANDBY_JPEG in frame


async def test_healthz_uptime(api) -> None:
    client, _ = api
    body = (await client.get("/api/v1/healthz")).json()
    assert body["status"] == "ok" and body["uptime_s"] >= 0


async def test_streaming_routes_documented(api) -> None:
    client, _ = api
    paths = set((await client.get("/openapi.json")).json()["paths"])
    assert {"/api/v1/events", "/api/v1/camera/stream", "/api/v1/camera/snapshot"} <= paths
