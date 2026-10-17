from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from marshal_api.core.errors import ApiError
from marshal_api.services.batch_service import BatchService
from marshal_api.services.batch_store import MemoryBatchStore


def _fake_lines(request):
    manifest = json.loads(Path(request.manifest_path).read_text())
    cars = {}
    functional = {}
    shipped = {truck["truck_id"]: [] for truck in manifest["trucks"]}
    labels = {car["expected"]["kind"]: car for car in manifest["cars"]}
    for car in manifest["cars"]:
        vin, kind = car["vin"], car["expected"]["kind"]
        state = "rework" if kind in ("wrong_colour", "dead_lamp") else "shipped"
        if state == "shipped":
            shipped[car["truck_id"]].append(vin)
        cars[vin] = {
            "state": state,
            "location": "production_control" if state == "rework" else "shipped",
            "truck": car["truck_id"],
            "gate": "FAIL" if kind == "wrong_colour" else "PASS",
            "report": "FAIL" if kind in ("wrong_colour", "dead_lamp") else "PASS",
            "mismatches": ["colour mismatch"] if kind == "wrong_colour" else [],
            "booked_bays": ["production_control"] if kind == "wrong_colour" else [],
        }
        if kind == "dead_lamp":
            functional[vin] = {"verdict": "FAIL", "disagreements": [car["expected"]["fault_step"]],
                               "checklist": [{"name": car["expected"]["fault_step"],
                                              "telemetry_ok": True, "camera_ok": False}]}
    incident_car = labels.get("incident")
    incident = {"enabled": bool(incident_car), "vin": incident_car["vin"] if incident_car else None,
                "case_id": "AC-1" if incident_car else None,
                "resolved": bool(incident_car),
                "test_requests": [{"text": "A box and worker blocked the lane.", "source": "assistance case AC-1"}]
                if incident_car else []}
    return [
        {"type": "plant.start", "cars": request.vins},
        *[{"type": "plant.car", "vin": vin, "state": data["state"], "location": data["location"],
           "gate_verdict": data["gate"], "report_verdict": data["report"]}
          for vin, data in cars.items()],
        {"type": "batch.event", "flow": 3, "source": "Visual inspector", "title": "Gate verdicts",
         "detail": "sample batch", "level": "info"},
        {"type": "plant.done", "result": "success", "cars": cars, "functional": functional,
         "incident": incident, "shipped": shipped},
    ]


class FakeRunner:
    def __init__(self):
        self.terminated = False
        self.started = asyncio.Event()
        self.stop = asyncio.Event()

    async def stream(self, request):
        self.started.set()
        for line in _fake_lines(request):
            yield line

    async def stream_lab(self, request):
        yield {"type": "lab.generated", "scenario_id": "SC-1", "status": "dry_run_ok"}
        yield {"type": "lab.adversarial.done", "runs": 4, "robust": False,
               "minimal": {"variant": "SC-1-v2"}}

    async def terminate(self):
        self.terminated = True
        self.stop.set()


class SlowRunner(FakeRunner):
    async def stream(self, request):
        self.started.set()
        await self.stop.wait()
        if False:
            yield {}


SETTINGS = SimpleNamespace(batch_enabled=True, batch_max_cars=6, batch_timeout_s=30,
                           batch_lab_timeout_s=2, batch_lab_adversarial=4, model="gemini-test",
                           batch_plant_script="scripts/run_carla_plant.sh",
                           batch_lab_script="scripts/run_carla_lab.sh")


def _service(store, runner, *, settings=SETTINGS) -> BatchService:
    return BatchService(store, runner, "t1", settings=settings, llm_mode="offline")


async def test_batch_run_persists_cars_labels_lab_and_report() -> None:
    store, runner = MemoryBatchStore(), FakeRunner()
    service = _service(store, runner)
    accepted = await service.create_batch(3, seed=7)
    assert accepted.status == "queued"
    await asyncio.wait_for(service._task, timeout=5)

    detail = await service.get_batch(accepted.batch_id)
    report = await service.get_report(accepted.batch_id)
    assert detail.status == "done" and detail.progress == 100
    assert len(detail.cars) == 3 and len(report.gemini["per_flow"]) == 7
    assert report.labels["caught"] == 3 and report.labels["missed"] == 0
    assert report.gemini["degraded"] is True
    assert detail.flow_counts.flow6.status == "generated"
    assert detail.flow_counts.flow7.status == "done"
    events = await service.get_events(accepted.batch_id)
    assert events.items[0].seq == 1
    assert await store.list_active() == []


async def test_one_car_skips_incident_lab() -> None:
    service = _service(MemoryBatchStore(), FakeRunner())
    accepted = await service.create_batch(1, seed=7)
    await asyncio.wait_for(service._task, timeout=5)
    detail = await service.get_batch(accepted.batch_id)
    assert detail.status == "done" and not detail.incident_enabled
    assert detail.flow_counts.flow6.status == "not_triggered"
    assert detail.flow_counts.flow7.status == "not_triggered"


async def test_second_active_batch_is_rejected_and_cancel_stops_runner() -> None:
    store, runner = MemoryBatchStore(), SlowRunner()
    service = _service(store, runner)
    first = await service.create_batch(1, seed=1)
    await asyncio.wait_for(runner.started.wait(), timeout=2)
    with pytest.raises(ApiError) as conflict:
        await service.create_batch(1, seed=2)
    assert conflict.value.status_code == 409
    cancelled = await service.cancel(first.batch_id)
    assert cancelled.status == "cancelled"
    await asyncio.wait_for(service._task, timeout=2)
    assert runner.terminated
    detail = await service.get_batch(first.batch_id)
    assert detail.status == "cancelled"


async def test_timeout_marks_batch_failed() -> None:
    settings = SimpleNamespace(**{**SETTINGS.__dict__, "batch_timeout_s": 0.01})
    service = _service(MemoryBatchStore(), SlowRunner(), settings=settings)
    accepted = await service.create_batch(1, seed=1)
    await asyncio.wait_for(service._task, timeout=2)
    detail = await service.get_batch(accepted.batch_id)
    assert detail.status == "failed"
    with pytest.raises(ApiError) as not_ready:
        await service.get_report(accepted.batch_id)
    assert not_ready.value.status_code == 409


async def test_batch_is_disabled_until_host_opt_in() -> None:
    settings = SimpleNamespace(**{**SETTINGS.__dict__, "batch_enabled": False})
    service = _service(MemoryBatchStore(), FakeRunner(), settings=settings)
    with pytest.raises(ApiError) as disabled:
        await service.create_batch(1)
    assert disabled.value.status_code == 503
