"""Sprint 3 gate: QualityService inspections, reports and rework loop."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import marshal_api.models  # noqa: F401  (registers tables)
from marshal_ai.events import InMemoryBus
from marshal_ai.plant.state import PlantState

from marshal_api.bridge import get_event_bus, get_mission_client, get_plant_state
from marshal_api.core.deps import get_current_tenant, get_session
from marshal_api.main import app
from marshal_api.models import (
    BuildsheetMismatch as MismatchRow,
    Car,
    CarReport as ReportRow,
    CarState,
    FunctionalStepCheck as StepRow,
    InspectionResult as ResultRow,
    Plant,
    ReworkBay as BayRow,
    ReworkTicket as TicketRow,
    Tenant,
    Verdict,
    VisualFinding as FindingRow,
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
                    state=CarState.REWORK, location="rework", flags=["rework"]))
    await session.flush()
    session.add_all(
        [
            ResultRow(id="irv1", tenant_id=TENANT, plant_id="p1", result_id="IR-V1",
                      car_id="c1", kind="visual", verdict=Verdict.REVIEW, review_status="pending"),
            ResultRow(id="irf1", tenant_id=TENANT, plant_id="p1", result_id="IR-F1",
                      car_id="c1", kind="functional", verdict=Verdict.FAIL, review_status="pending"),
            ReportRow(id="rp1", tenant_id=TENANT, plant_id="p1", car_id="c1", verdict=Verdict.REVIEW,
                      summary="needs paint", reinspection_status="not_required"),
            TicketRow(id="tk1", tenant_id=TENANT, plant_id="p1", ticket_id="T-1", car_id="c1",
                      bay_code="rework.paint", defect_type="scratch", status="open", reason="scratch"),
            BayRow(id="b1", tenant_id=TENANT, plant_id="p1", code="rework.paint", name="Paint",
                   defect_type="paint", capacity=1),
        ]
    )
    await session.flush()
    session.add_all(
        [
            FindingRow(id="f1", inspection_result_id="irv1", zone="rear_left_door",
                       defect_type="scratch", size_cm=3.0, confidence=0.9, observation="scratch"),
            FindingRow(id="f2", inspection_result_id="irv1", zone="hood",
                       defect_type="dent", size_cm=1.5, confidence=0.8, observation="dent"),
            FindingRow(id="f3", inspection_result_id="irv1", zone="roof",
                       defect_type="paint", size_cm=None, confidence=0.7, observation="faint"),
            MismatchRow(id="m1", inspection_result_id="irv1", attribute="color",
                        expected="blue", found="red", confidence=0.95),
            StepRow(id="s1", inspection_result_id="irf1", step_number=1, name="headlights",
                    telemetry_ok=True, camera_ok=True, camera_confidence=0.9, disagree=False),
            StepRow(id="s2", inspection_result_id="irf1", step_number=2, name="brake_lights",
                    telemetry_ok=False, camera_ok=True, camera_confidence=0.6, disagree=True),
        ]
    )
    await session.commit()


@pytest.fixture
async def api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quality.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await _seed(session)

    async def _session():
        async with maker() as session:
            yield session

    recorder = _Recorder()
    plant_state = PlantState()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_tenant] = lambda: TENANT
    app.dependency_overrides[get_event_bus] = lambda: InMemoryBus()
    app.dependency_overrides[get_mission_client] = lambda: recorder
    app.dependency_overrides[get_plant_state] = lambda: plant_state

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker, recorder

    app.dependency_overrides.clear()
    await engine.dispose()


def _worker() -> dict[str, str]:
    return {"X-Actor-Role": "worker", "X-Tenant-ID": TENANT}


async def test_visual_joins(api) -> None:
    client, _, _ = api
    body = (await client.get("/api/v1/cars/VIN-1/inspection/visual", headers={"X-Tenant-ID": TENANT})).json()
    assert body["verdict"] == "REVIEW"
    assert len(body["findings"]) == 3
    assert len(body["mismatches"]) == 1
    assert body["mismatches"][0]["attribute"] == "color"
    assert body["findings"][0]["size"] == "3 cm"


async def test_functional_joins(api) -> None:
    client, _, _ = api
    body = (await client.get("/api/v1/cars/VIN-1/inspection/functional", headers={"X-Tenant-ID": TENANT})).json()
    assert [s["step"] for s in body["steps"]] == [1, 2]
    assert body["steps"][1]["disagree"] is True and body["steps"][1]["telemetry"] == "fail"


async def test_list_results_filters_and_counts(api) -> None:
    client, _, _ = api
    body = (await client.get("/api/v1/inspection/results", headers={"X-Tenant-ID": TENANT})).json()
    assert body["pagination"]["total"] == 2
    by_kind = {r["kind"]: r for r in body["items"]}
    assert by_kind["visual"]["finding_count"] == 3
    assert by_kind["functional"]["failed_check_count"] == 1

    visual_only = (
        await client.get("/api/v1/inspection/results", params={"kind": "visual"}, headers={"X-Tenant-ID": TENANT})
    ).json()
    assert [r["result_id"] for r in visual_only["items"]] == ["IR-V1"]


async def test_inspection_kpis(api) -> None:
    client, _, _ = api
    body = (await client.get("/api/v1/inspection/kpis", headers={"X-Tenant-ID": TENANT})).json()
    assert body["first_pass_yield"] == 0.0
    assert body["review_share"] == 0.5
    assert body["failure_count"] == 1


async def test_report_detail(api) -> None:
    client, _, _ = api
    body = (await client.get("/api/v1/cars/VIN-1/report", headers={"X-Tenant-ID": TENANT})).json()
    assert body["verdict"] == "REVIEW"
    assert len(body["visual_findings"]) == 3
    assert len(body["functional_failures"]) == 1
    assert len(body["rework_tickets"]) == 1
    assert body["reinspection_status"] == "NOT_REQUIRED"
    assert body["open_findings"] == 4


async def test_rework_done_loop(api) -> None:
    client, maker, recorder = api
    response = await client.post("/api/v1/cars/VIN-1/rework/done", json={"actor_id": "wrk-1"}, headers=_worker())
    assert response.status_code == 200 and response.json()["status"] == "DONE"

    assert len(recorder.sent) == 1 and recorder.sent[0].waypoints == ["inspection_gate"]

    async with maker() as session:
        ticket = (await session.execute(select(TicketRow).where(TicketRow.ticket_id == "T-1"))).scalars().one()
        assert ticket.status == "done" and ticket.done_by == "wrk-1"
        report = (await session.execute(select(ReportRow).where(ReportRow.car_id == "c1"))).scalars().one()
        assert report.reinspection_status == "waiting"
        car = (await session.execute(select(Car).where(Car.vin == "VIN-1"))).scalars().one()
        assert car.state == CarState.WAITING_DISPATCH and "rework" not in car.flags


async def test_worker_role_guard(api) -> None:
    client, _, _ = api
    response = await client.post(
        "/api/v1/cars/VIN-1/rework/done",
        json={"actor_id": "ins-1"},
        headers={"X-Actor-Role": "inspector", "X-Tenant-ID": TENANT},
    )
    assert response.status_code == 403


async def test_rework_bays(api) -> None:
    client, _, _ = api
    body = (await client.get("/api/v1/rework/bays", headers={"X-Tenant-ID": TENANT})).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["bay_id"] == "rework.paint" and body["items"][0]["capacity"] == 1
