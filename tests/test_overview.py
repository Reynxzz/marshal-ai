"""Sprint 5 gate: live overview and KPI aggregations."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import marshal_api.models  # noqa: F401  (registers tables)
from marshal_ai.events import InMemoryBus
from marshal_ai.plant.state import PlantState

from marshal_api.bridge import get_event_bus, get_mission_client, get_plant_state
from marshal_api.core.deps import get_current_tenant, get_session
from marshal_api.main import app
from marshal_api.models import (
    AssistanceCase as CaseRow,
    AssistanceStatus,
    Car,
    CarState,
    InspectionResult as ResultRow,
    Plant,
    Proposal as ProposalRow,
    Release as ReleaseRow,
    Scenario as ScenarioRow,
    ScenarioStatus,
    Tenant,
    Verdict,
)

TENANT = "t1"


async def _seed(session: AsyncSession) -> None:
    session.add(Tenant(id=TENANT, name="Alpha", slug="alpha"))
    await session.flush()
    session.add(Plant(id="p1", tenant_id=TENANT, code="P1", name="Plant Alpha"))
    await session.flush()
    session.add_all(
        [
            Car(id="c1", tenant_id=TENANT, plant_id="p1", vin="VIN-1", state=CarState.DRIVING, location="yard"),
            Car(id="c2", tenant_id=TENANT, plant_id="p1", vin="VIN-2", state=CarState.WAITING_DISPATCH, location="yard"),
            Car(id="c3", tenant_id=TENANT, plant_id="p1", vin="VIN-3", state=CarState.EXCEPTION, location="yard"),
            Car(id="c4", tenant_id=TENANT, plant_id="p1", vin="VIN-4", state=CarState.HELD, location="yard"),
            Car(id="c5", tenant_id=TENANT, plant_id="p1", vin="VIN-5", state=CarState.YARD, location="yard"),
            CaseRow(id="a1", tenant_id=TENANT, plant_id="p1", case_id="CASE-1", car_id="c3",
                    location="yard", situation="box", status=AssistanceStatus.OPEN),
            CaseRow(id="a2", tenant_id=TENANT, plant_id="p1", case_id="CASE-0", car_id="c3",
                    location="yard", situation="done", status=AssistanceStatus.RESOLVED),
            ProposalRow(id="pr1", tenant_id=TENANT, plant_id="p1", proposal_id="P-1", kind="replan",
                        category="replan", summary="pending", status="pending"),
            ProposalRow(id="pr2", tenant_id=TENANT, plant_id="p1", proposal_id="P-2", kind="replan",
                        category="replan", summary="done", status="approved"),
            ResultRow(id="ir1", tenant_id=TENANT, plant_id="p1", result_id="IR-1", car_id="c1",
                      kind="visual", verdict=Verdict.PASS, review_status="done"),
            ResultRow(id="ir2", tenant_id=TENANT, plant_id="p1", result_id="IR-2", car_id="c3",
                      kind="functional", verdict=Verdict.FAIL, review_status="pending"),
            ScenarioRow(id="sc1", tenant_id=TENANT, plant_id="p1", scenario_id="SC-1",
                        source="manual", status=ScenarioStatus.VALID, summary="pending"),
            ReleaseRow(id="rel1", tenant_id=TENANT, release_id="REL-1", name="agent",
                       evaluation_status="done", gate="blocked", passed_count=1, failed_count=1),
        ]
    )
    await session.commit()


@pytest.fixture
async def api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'overview.db'}")
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
    app.dependency_overrides[get_event_bus] = lambda: InMemoryBus()
    app.dependency_overrides[get_mission_client] = lambda: None
    app.dependency_overrides[get_plant_state] = lambda: PlantState()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_overview_aggregates(api) -> None:
    client = api
    body = (await client.get("/api/v1/overview", headers={"X-Tenant-ID": TENANT})).json()
    assert body["plant"] == "Plant Alpha"
    assert body["cars"]["total"] == 5
    assert body["cars"]["by_state"]["driving"] == 1
    assert body["cars"]["exception"] == 1 and body["cars"]["held"] == 1
    assert body["assistance_open"] == 1
    assert body["proposals_pending"] == 1
    assert body["lab"] == {"scenarios_pending_review": 1, "releases_blocked": 1}
    assert len(body["attention"]["assistance"]) == 1
    assert len(body["attention"]["proposals"]) == 1
    assert {c["vin"] for c in body["yard_exceptions"]} == {"VIN-3", "VIN-4"}


async def test_kpis_aggregate(api) -> None:
    client = api
    body = (await client.get("/api/v1/kpis", headers={"X-Tenant-ID": TENANT})).json()
    by_key = {k["key"]: k["value"] for k in body["items"]}
    assert by_key["active"] == 2
    assert by_key["assistance_open"] == 1
    assert by_key["proposals_pending"] == 1
    assert by_key["exception"] == 2
    assert by_key["first_pass"] == 50
    assert by_key["releases_blocked"] == 1


async def test_readyz_reports_dependencies(api) -> None:
    client = api
    body = (await client.get("/api/v1/readyz")).json()
    assert body["status"] == "ready"
    names = {d["name"] for d in body["dependencies"]}
    assert {"database", "event-bus", "carla"} <= names
