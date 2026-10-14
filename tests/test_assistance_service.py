"""Sprint 2 gate: AssistanceService decision desk against a seeded, isolated DB."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import marshal_api.models  # noqa: F401  (registers tables)
from marshal_ai.events import InMemoryBus

from marshal_api.bridge import get_event_bus, get_mission_client, get_plant_state
from marshal_api.core.deps import get_current_tenant, get_session
from marshal_api.main import app
from marshal_api.models import (
    AssistanceCase as AssistanceCaseRow,
    AssistanceOption as AssistanceOptionRow,
    AssistanceStatus,
    Car,
    CarState,
    Plant,
    Tenant,
)

TENANT = "t1"


class _Recorder:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, command) -> None:
        self.sent.append(command)


async def _seed(session: AsyncSession) -> None:
    session.add(Tenant(id=TENANT, name="Alpha", slug="alpha"))
    await session.flush()
    session.add(Plant(id="p1", tenant_id=TENANT, code="P1", name="Plant Alpha"))
    await session.flush()
    session.add(Car(id="c1", tenant_id=TENANT, plant_id="p1", vin="VIN-1",
                    state=CarState.EXCEPTION, location="zone-c"))
    await session.flush()
    session.add_all(
        [
            AssistanceCaseRow(
                id="ac1", tenant_id=TENANT, plant_id="p1", case_id="CASE-1", car_id="c1",
                location="zone-c", situation="box in lane", status=AssistanceStatus.OPEN,
                recommended_option_id="wait", confidence=0.8,
            ),
            AssistanceCaseRow(
                id="ac2", tenant_id=TENANT, plant_id="p1", case_id="CASE-2", car_id="c1",
                location="zone-c", situation="forklift", status=AssistanceStatus.OPEN,
            ),
        ]
    )
    await session.flush()
    session.add_all(
        [
            AssistanceOptionRow(id="o2", case_id="ac1", option_id="wait", label="Wait", risk="low", rank=1),
            AssistanceOptionRow(id="o1", case_id="ac1", option_id="reverse", label="Reverse", risk="medium", rank=2),
            AssistanceOptionRow(id="o3", case_id="ac1", option_id="reroute", label="Re-route", risk="high", rank=3),
        ]
    )
    await session.commit()


@pytest.fixture
async def api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'assist.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await _seed(session)

    async def _session():
        async with maker() as session:
            yield session

    recorder = _Recorder()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_tenant] = lambda: TENANT
    app.dependency_overrides[get_event_bus] = lambda: InMemoryBus()
    app.dependency_overrides[get_mission_client] = lambda: recorder
    app.dependency_overrides[get_plant_state] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker, recorder

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_get_case_returns_ranked_options(api) -> None:
    client, _, _ = api
    body = (await client.get("/api/v1/assistance/cases/CASE-1", headers={"X-Tenant-ID": TENANT})).json()
    assert body["vin"] == "VIN-1" and body["status"] == "OPEN"
    assert [o["id"] for o in body["options"]] == ["wait", "reverse", "reroute"]
    assert body["recommendation"] == {"option_id": "wait", "confidence": 0.8, "note": None}


async def test_list_and_open_filter(api) -> None:
    client, _, _ = api
    body = (await client.get("/api/v1/assistance/cases", headers={"X-Tenant-ID": TENANT})).json()
    assert body["pagination"]["total"] == 2

    open_only = (
        await client.get(
            "/api/v1/assistance/cases", params={"open": "true"}, headers={"X-Tenant-ID": TENANT}
        )
    ).json()
    assert {c["case_id"] for c in open_only["items"]} == {"CASE-1", "CASE-2"}


async def test_unsupported_option_is_422(api) -> None:
    client, _, _ = api
    response = await client.post(
        "/api/v1/assistance/cases/CASE-1/decide",
        json={"option": "teleport", "actor_id": "sup-1"},
        headers={"X-Actor-Role": "supervisor", "X-Tenant-ID": TENANT},
    )
    assert response.status_code == 422 and response.json()["code"] == "option_not_allowed"


async def test_decide_resolves_case_and_dispatches(api) -> None:
    client, maker, recorder = api
    response = await client.post(
        "/api/v1/assistance/cases/CASE-1/decide",
        json={"option": "wait", "actor_id": "sup-1"},
        headers={"X-Actor-Role": "supervisor", "X-Tenant-ID": TENANT},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "RESOLVED" and response.json()["chosen_option"] == "wait"

    assert len(recorder.sent) == 1
    command = recorder.sent[0]
    assert command.case_id == "CASE-1" and command.option == "wait" and command.chosen_by == "sup-1"

    async with maker() as session:
        case = (await session.execute(select(AssistanceCaseRow).where(AssistanceCaseRow.case_id == "CASE-1"))).scalars().one()
        assert case.status == AssistanceStatus.RESOLVED
        assert case.choice == "wait" and case.resolved_by == "sup-1"
        car = (await session.execute(select(Car).where(Car.vin == "VIN-1"))).scalars().one()
        assert car.state == CarState.WAITING_DISPATCH

    again = await client.post(
        "/api/v1/assistance/cases/CASE-1/decide",
        json={"option": "wait", "actor_id": "sup-1"},
        headers={"X-Actor-Role": "supervisor", "X-Tenant-ID": TENANT},
    )
    assert again.status_code == 409


async def test_kpis_reflect_resolution(api) -> None:
    client, _, _ = api
    await client.post(
        "/api/v1/assistance/cases/CASE-1/decide",
        json={"option": "wait", "actor_id": "sup-1"},
        headers={"X-Actor-Role": "supervisor", "X-Tenant-ID": TENANT},
    )
    body = (await client.get("/api/v1/assistance/kpis", headers={"X-Tenant-ID": TENANT})).json()
    assert body["open"] == 1 and body["resolved"] == 1
    assert body["avg_latency_minutes"] >= 0
