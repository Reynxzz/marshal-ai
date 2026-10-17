"""The API's in-process demo plant (marshal_api.plant): the controls behind the dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from marshal_ai.config import Settings
from marshal_ai.offline import offline_llm
from marshal_ai.plant.state import PlantState, Truck
from marshal_ai.records import Car
from marshal_ai.runtime import build_runtime

from marshal_api import plant


def _runtime():
    s = Settings(driving="fake", store="memory", llm="offline", artifacts="none", history="none")
    return build_runtime(s)


def test_a_spawn_adds_a_clean_car_to_the_line() -> None:
    rt = _runtime()

    vin = plant._spawn(rt, 5)

    assert vin == "VIN-P5"
    car = rt.store.get(Car, vin)
    assert car is not None and car.location == "line_end"
    assert car.build_sheet.truck_id == "T-07"          # so a truck can load it later
    assert rt.driving.cars[vin].location == "line_end"
    assert plant._in_flight(rt) == 1


def test_a_due_truck_ships_its_cars_and_is_due_again() -> None:
    now = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
    shipped: list[str] = []

    class Yard:
        def ship(self, truck_id: str) -> list[str]:
            shipped.append(truck_id)
            return ["VIN-P1"]

    state = PlantState()
    state.trucks["T-07"] = Truck("T-07", now - timedelta(minutes=30), now - timedelta(minutes=2))
    state.trucks["T-08"] = Truck("T-08", now, now + timedelta(minutes=30))
    app = SimpleNamespace(rt=SimpleNamespace(clock=lambda: now, plant=state), yard=Yard())

    plant._load_due_trucks(app)

    assert shipped == ["T-07"]                          # the late one; the other is not due yet
    assert state.trucks["T-07"].eta - now == timedelta(minutes=plant.TRUCK_CYCLE_MIN)
    assert state.trucks["T-08"].eta - now == timedelta(minutes=30)


async def test_the_controller_spawns_raises_a_case_decides_and_restarts(monkeypatch) -> None:
    monkeypatch.setattr(plant, "_llm", lambda: offline_llm())
    p = plant.Plant()
    assert p.state()["running"] is False                # before start: the API says so

    await p.start(speed=8)
    try:
        assert p.state()["running"] is True
        vin = p.spawn_now()
        assert vin.startswith("VIN-P") and p.state()["cars"]

        case = await p.exception()
        assert case["vin"] and case["status"] == "OPEN"
        assert {o["id"] for o in case["options"]} == set(plant.ASSIST_OPTIONS)
        assert [o["rank"] for o in case["options"]] == [1, 2, 3]   # the desk's order
        assert case["recommended"] in plant.ASSIST_OPTIONS

        decided = await p.decide(case["case_id"], case["options"][0]["id"], "tester")
        assert decided["status"] == "RESOLVED" and decided["choice"] == case["options"][0]["id"]
        assert p.state()["assist"] is None              # the case is closed

        await p.restart()
        assert p.running and p.state()["running"] is True
    finally:
        await p.stop()
    assert p.state()["running"] is False


async def test_the_endpoints_refuse_when_the_plant_is_not_running() -> None:
    p = plant.Plant()
    try:
        p.spawn_now()
    except plant.PlantNotRunning:
        pass
    else:
        raise AssertionError("spawn without a plant must fail")
