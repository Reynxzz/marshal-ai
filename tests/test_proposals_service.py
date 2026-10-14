"""Sprint 2 gate: ProposalService decision gate against a seeded, isolated DB."""

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
    Car,
    CarState,
    InspectionResult,
    Plant,
    Proposal as ProposalRow,
    ReworkTicket,
    Tenant,
)

TENANT = "t1"


async def _seed(session: AsyncSession) -> None:
    session.add(Tenant(id=TENANT, name="Alpha", slug="alpha"))
    await session.flush()
    session.add(Plant(id="p1", tenant_id=TENANT, code="P1", name="Plant Alpha"))
    await session.flush()
    session.add(Car(id="c1", tenant_id=TENANT, plant_id="p1", vin="VIN-1",
                    state=CarState.REWORK, location="rework"))
    await session.flush()
    session.add_all(
        [
            ProposalRow(
                id="pr1", tenant_id=TENANT, plant_id="p1", proposal_id="P-1", kind="rework",
                category="rework", car_id="c1", summary="Rework rear door",
                payload={"ticket_id": "T-1"}, status="pending",
                allowed_actions=["approve", "reject"],
                effects={"approve": "Send to rework", "reject": "Keep as is"},
            ),
            ProposalRow(
                id="pr2", tenant_id=TENANT, plant_id="p1", proposal_id="P-2",
                kind="inspection_review", category="inspection", car_id="c1",
                summary="Confirm finding", payload={"result_id": "IR-1"}, status="pending",
                allowed_actions=["confirm", "reject"], effects={},
            ),
            ReworkTicket(id="rt1", tenant_id=TENANT, plant_id="p1", ticket_id="T-1", car_id="c1",
                         bay_code="rework.paint", defect_type="scratch", status="open", reason="scratch"),
            InspectionResult(id="ir1", tenant_id=TENANT, plant_id="p1", result_id="IR-1",
                             car_id="c1", kind="visual", review_status="pending"),
        ]
    )
    await session.commit()


@pytest.fixture
async def api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'proposals.db'}")
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
    app.dependency_overrides[get_plant_state] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_list_proposals_filters_and_maps_vin(api) -> None:
    client, _ = api
    body = (await client.get("/api/v1/proposals", headers={"X-Tenant-ID": TENANT})).json()
    assert body["pagination"]["total"] == 2
    assert all(p["vin"] == "VIN-1" for p in body["items"])

    rework = (
        await client.get("/api/v1/proposals", params={"kind": "rework"}, headers={"X-Tenant-ID": TENANT})
    ).json()
    assert [p["proposal_id"] for p in rework["items"]] == ["P-1"]
    assert rework["items"][0]["effects"]["approve"] == "Send to rework"


async def test_unknown_decision_rejected(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/v1/proposals/P-1/decide",
        json={"decision": "nope", "actor_id": "sup-1"},
        headers={"X-Actor-Role": "supervisor", "X-Tenant-ID": TENANT},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_rework_approval_side_effect_and_conflict(api) -> None:
    client, maker = api
    first = await client.post(
        "/api/v1/proposals/P-1/decide",
        json={"decision": "approve", "actor_id": "sup-1"},
        headers={"X-Actor-Role": "supervisor", "X-Tenant-ID": TENANT},
    )
    assert first.status_code == 200 and first.json()["status"] == "approved"

    async with maker() as session:
        ticket = (await session.execute(select(ReworkTicket).where(ReworkTicket.ticket_id == "T-1"))).scalars().one()
        assert ticket.status == "in_repair"

    again = await client.post(
        "/api/v1/proposals/P-1/decide",
        json={"decision": "reject", "actor_id": "sup-1"},
        headers={"X-Actor-Role": "supervisor", "X-Tenant-ID": TENANT},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "proposal_already_decided"


async def test_inspection_review_confirm_clears_review(api) -> None:
    client, maker = api
    response = await client.post(
        "/api/v1/proposals/P-2/decide",
        json={"decision": "confirm", "actor_id": "sup-2"},
        headers={"X-Actor-Role": "supervisor", "X-Tenant-ID": TENANT},
    )
    assert response.status_code == 200 and response.json()["decision"] == "confirm"

    async with maker() as session:
        result = (await session.execute(select(InspectionResult).where(InspectionResult.result_id == "IR-1"))).scalars().one()
        assert result.review_status == "done" and result.reviewed_by == "sup-2"


async def test_missing_proposal_is_404(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/v1/proposals/NOPE/decide",
        json={"decision": "approve", "actor_id": "sup-1"},
        headers={"X-Actor-Role": "supervisor", "X-Tenant-ID": TENANT},
    )
    assert response.status_code == 404
