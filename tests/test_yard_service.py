"""Sprint 1 gate: YardService against a seeded, isolated SQLite database."""

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
    BuildSheet as BuildSheetRow,
    Car,
    CarState,
    Plant,
    ReworkBay as ReworkBayRow,
    Tenant,
    Truck as TruckRow,
    Zone as ZoneRow,
)

TENANT = "t1"


async def _seed(session: AsyncSession) -> None:
    session.add(Tenant(id=TENANT, name="Alpha", slug="alpha"))
    await session.flush()
    session.add(Plant(id="p1", tenant_id=TENANT, code="P1", name="Plant Alpha"))
    await session.flush()
    session.add(TruckRow(id="tr1", tenant_id=TENANT, plant_id="p1", code="T-1", capacity=10))
    await session.flush()
    session.add_all(
        [
            ZoneRow(id="z1", tenant_id=TENANT, plant_id="p1", code="zone-a", name="Zone A"),
            ReworkBayRow(
                id="b1", tenant_id=TENANT, plant_id="p1", code="rework.paint",
                name="Paint", defect_type="paint", capacity=1,
            ),
            Car(id="c1", tenant_id=TENANT, plant_id="p1", vin="VIN-1",
                state=CarState.YARD, location="yard", truck_id="tr1"),
            Car(id="c2", tenant_id=TENANT, plant_id="p1", vin="VIN-2",
                state=CarState.REWORK, location="rework", truck_id="tr1"),
            Car(id="c3", tenant_id=TENANT, plant_id="p1", vin="VIN-3",
                state=CarState.YARD, location="yard"),
            Car(id="c4", tenant_id=TENANT, plant_id="p1", vin="VIN-4",
                state=CarState.HELD, location="yard", flags=["held"]),
        ]
    )
    session.add(
        BuildSheetRow(id="bs1", car_id="c1", model="E", color="blue", options=["sunroof"])
    )
    await session.commit()


@pytest.fixture
async def api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'yard.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await _seed(session)

    async def _session():
        async with maker() as session:
            yield session

    plant_state = PlantState()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_tenant] = lambda: TENANT
    app.dependency_overrides[get_event_bus] = lambda: InMemoryBus()
    app.dependency_overrides[get_mission_client] = lambda: None
    app.dependency_overrides[get_plant_state] = lambda: plant_state

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()


def _supervisor() -> dict[str, str]:
    return {"X-Actor-Role": "supervisor"}


async def test_yard_snapshot(api) -> None:
    client, _ = api
    body = (await client.get("/api/v1/yard", headers={"X-Tenant-ID": TENANT})).json()

    assert body["plant"] == "Plant Alpha"
    assert body["summary"] == {"total": 4, "exception": 0, "held": 1, "zones_closed": 0}
    assert body["zones"] == [{"zone": "zone-a", "name": "Zone A", "closed": False, "reason": None}]
    truck = body["trucks"][0]
    assert truck["truck_id"] == "T-1"
    assert sorted(truck["assigned_vins"]) == ["VIN-1", "VIN-2"]


async def test_get_car_joins_build_sheet(api) -> None:
    client, _ = api
    body = (await client.get("/api/v1/cars/VIN-1", headers={"X-Tenant-ID": TENANT})).json()
    assert body["state"] == "parked"
    assert body["build_sheet"] == {"model": "E", "trim": "standard", "color": "blue", "options": ["sunroof"]}

    missing = await client.get("/api/v1/cars/NOPE", headers={"X-Tenant-ID": TENANT})
    assert missing.status_code == 404


async def test_list_cars_filtering(api) -> None:
    client, _ = api
    by_state = (
        await client.get("/api/v1/cars", params={"state": "yard"}, headers={"X-Tenant-ID": TENANT})
    ).json()
    assert sorted(c["vin"] for c in by_state["items"]) == ["VIN-1", "VIN-3"]
    assert all(c["state"] == "parked" for c in by_state["items"])

    substring = (
        await client.get("/api/v1/cars", params={"q": "-1"}, headers={"X-Tenant-ID": TENANT})
    ).json()
    assert [c["vin"] for c in substring["items"]] == ["VIN-1"]

    paged = (
        await client.get(
            "/api/v1/cars", params={"page": 2, "limit": 2}, headers={"X-Tenant-ID": TENANT}
        )
    ).json()
    assert paged["pagination"] == {"page": 2, "limit": 2, "total": 4, "last_page": 2}
    assert len(paged["items"]) == 2


async def test_hold_and_resume_lifecycle(api) -> None:
    client, _ = api
    held = await client.post(
        "/api/v1/cars/VIN-3/hold",
        json={"reason": "blocked"},
        headers={**_supervisor(), "X-Tenant-ID": TENANT},
    )
    assert held.status_code == 200 and held.json()["state"] == "held"

    body = (await client.get("/api/v1/cars/VIN-3", headers={"X-Tenant-ID": TENANT})).json()
    assert body["state"] == "held" and "held" in body["flags"]

    resumed = await client.post(
        "/api/v1/cars/VIN-3/resume", headers={**_supervisor(), "X-Tenant-ID": TENANT}
    )
    assert resumed.status_code == 200 and resumed.json()["state"] == "waiting_dispatch"

    body = (await client.get("/api/v1/cars/VIN-3", headers={"X-Tenant-ID": TENANT})).json()
    assert body["state"] == "waiting_dispatch" and "held" not in body["flags"]


async def test_truck_load_guard(api) -> None:
    client, maker = api
    blocked = await client.post(
        "/api/v1/trucks/T-1/load", headers={**_supervisor(), "X-Tenant-ID": TENANT}
    )
    assert blocked.status_code == 400

    async with maker() as session:
        car = (await session.execute(select(Car).where(Car.vin == "VIN-2"))).scalars().one()
        car.state = CarState.YARD
        session.add(car)
        await session.commit()

    loaded = await client.post(
        "/api/v1/trucks/T-1/load", headers={**_supervisor(), "X-Tenant-ID": TENANT}
    )
    assert loaded.status_code == 200
    assert sorted(loaded.json()["shipped_vins"]) == ["VIN-1", "VIN-2"]


async def test_send_mission_dispatches(api) -> None:
    client, _ = api
    ok = await client.post(
        "/api/v1/cars/VIN-3/missions",
        json={"destination": "yard"},
        headers={**_supervisor(), "X-Tenant-ID": TENANT},
    )
    assert ok.status_code == 200 and ok.json()["state"] == "driving"

    held = await client.post(
        "/api/v1/cars/VIN-4/missions",
        json={"destination": "yard"},
        headers={**_supervisor(), "X-Tenant-ID": TENANT},
    )
    assert held.status_code == 400


async def test_zone_closure_and_late_truck(api) -> None:
    client, _ = api
    closed = await client.post(
        "/api/v1/zones/zone-a/closure",
        json={"closed": True, "reason": "spill"},
        headers={**_supervisor(), "X-Tenant-ID": TENANT},
    )
    assert closed.status_code == 200 and closed.json()["closed"] is True

    yard = (await client.get("/api/v1/yard", headers={"X-Tenant-ID": TENANT})).json()
    assert yard["summary"]["zones_closed"] == 1 and yard["zones"][0]["closed"] is True

    await client.post(
        "/api/v1/trucks/T-1/eta",
        json={"eta": "2026-10-10T10:00:00Z"},
        headers={**_supervisor(), "X-Tenant-ID": TENANT},
    )
    late = await client.post(
        "/api/v1/trucks/T-1/eta",
        json={"eta": "2026-10-10T10:20:00Z"},
        headers={**_supervisor(), "X-Tenant-ID": TENANT},
    )
    assert late.json()["delay_min"] == 20.0


async def test_map_falls_back_to_plant_yaml(api) -> None:
    client, _ = api
    body = (await client.get("/api/v1/map", headers={"X-Tenant-ID": TENANT})).json()
    assert body["points"] and all(p["kind"] == "point" for p in body["points"])


async def test_role_enforcement(api) -> None:
    client, _ = api
    worker = {"X-Actor-Role": "worker", "X-Tenant-ID": TENANT}
    calls = [
        ("post", "/api/v1/cars/VIN-1/missions", {"destination": "yard"}),
        ("post", "/api/v1/cars/VIN-1/hold", {"reason": "x"}),
        ("post", "/api/v1/cars/VIN-1/resume", None),
        ("post", "/api/v1/trucks/T-1/eta", {"eta": "2026-10-10T10:00:00Z"}),
    ]
    for method, path, body in calls:
        response = await client.post(path, json=body, headers=worker)
        assert response.status_code == 403, path
