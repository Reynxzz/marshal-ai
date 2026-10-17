"""Own the lifetime of a CARLA batch and persist its progress and result.

State lives in a :class:`BatchStore` (Firestore in production, memory in tests), never on the
ephemeral CARLA VM's disk. The service only orchestrates: manifest -> plant subprocess -> optional
lab -> report.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import signal
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

from marshal_ai.batch.feedback import (
    SYSTEM,
    BatchFeedback,
    build_digest,
    build_prompt,
    normalize_feedback,
    offline_feedback,
)
from marshal_ai.batch.manifest import build_manifest, is_finished, write_manifest
from marshal_ai.batch.report import build_report
from marshal_ai.batch.stages import stage_for
from marshal_ai.config import Settings as AgentSettings
from marshal_ai.llm import LLM
from marshal_api.config import Settings
from marshal_api.core.errors import ApiError
from marshal_api.models.base import generate_ulid
from marshal_api.schemas import (
    BatchAccepted,
    BatchCancelAccepted,
    BatchCarDto,
    BatchDetail,
    BatchEventDto,
    BatchEvents,
    BatchFlowCounts,
    BatchReport,
    BatchSummary,
    Page,
    Pagination,
)
from marshal_api.services.batch_store import BatchStore, utc_now_iso

log = logging.getLogger("marshal.api.batch")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TERMINAL = {"done", "failed", "cancelled"}
PERSISTED_FIELDS = (
    "status", "progress", "message", "created_cars", "finished_cars", "counts", "result",
    "lab", "report", "error", "started_at", "finished_at", "active_resource", "cars",
)
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    """A fire-and-forget task the loop keeps a reference to (a bare create_task can be GC'd)."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def default_flow_counts() -> dict[str, dict[str, Any]]:
    return {
        "flow1": {"released": 0, "waiting": 0, "in_yard": 0, "shipped": 0},
        "flow2": {"cases": 0, "open": 0, "resolved": 0, "escalated": 0},
        "flow3": {"pass": 0, "review": 0, "fail": 0},
        "flow4": {"pass": 0, "fail": 0, "disagreements": 0},
        "flow5": {"pass": 0, "rework": 0, "shipped": 0},
        "flow6": {"test_requests": 0, "generated": 0, "status": "not_triggered"},
        "flow7": {"runs": 0, "failures": 0, "status": "not_triggered"},
    }


@dataclass
class BatchRunRequest:
    batch_id: str
    manifest_path: str
    cars: int
    vins: list[str]
    prefix: str
    incident_vin: str | None
    script: str = "scripts/run_carla_plant.sh"
    events: bool = True
    timeout_s: int = 1800
    record: bool = False


@dataclass
class BatchLabRequest:
    batch_id: str
    situation: str
    source: str
    output: str
    script: str = "scripts/run_carla_lab.sh"
    adversarial_budget: int = 4
    timeout_s: int = 900


class BatchProcessRunner(Protocol):
    async def stream(self, request: BatchRunRequest) -> AsyncIterator[dict[str, Any]]: ...
    async def stream_lab(self, request: BatchLabRequest) -> AsyncIterator[dict[str, Any]]: ...
    async def terminate(self) -> None: ...


class SubprocessBatchRunner:
    """One process group at a time; stdout is JSONL and stderr stays out of the API response."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self.stderr_tail: deque[str] = deque(maxlen=200)

    async def _stream(self, script: str, env: dict[str, str]) -> AsyncIterator[dict[str, Any]]:
        if self._process is not None and self._process.returncode is None:
            raise RuntimeError("a CARLA batch process is already running")
        path = Path(script)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.is_file():
            raise FileNotFoundError(f"batch script not found: {path}")
        self.stderr_tail.clear()
        process = await asyncio.create_subprocess_exec(
            "bash", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, cwd=PROJECT_ROOT, start_new_session=True,
        )
        self._process = process

        async def drain_stderr() -> None:
            assert process.stderr is not None
            while line := await process.stderr.readline():
                self.stderr_tail.append(line.decode(errors="replace").rstrip()[:1000])

        stderr_task = asyncio.create_task(drain_stderr())
        plant_done = False
        try:
            assert process.stdout is not None
            while line := await process.stdout.readline():
                try:
                    value = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    plant_done |= value.get("type") in {"plant.done", "lab.adversarial.done"}
                    yield value
            code = await process.wait()
            await stderr_task
            if code and not plant_done:
                raise RuntimeError(f"batch subprocess exited {code}: {' | '.join(self.stderr_tail)[-1600:]}")
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
            if process.returncode is None:
                await self._terminate_process(process)
            if self._process is process:
                self._process = None

    async def stream(self, request: BatchRunRequest) -> AsyncIterator[dict[str, Any]]:
        env = {
            **os.environ,
            "MARSHAL_BATCH_ID": request.batch_id,
            "MARSHAL_PLANT_MANIFEST": request.manifest_path,
            "MARSHAL_PLANT_CARS": str(request.cars),
            "MARSHAL_PLANT_EVENTS": "1" if request.events else "0",
            "MARSHAL_PLANT_INCIDENT_VIN": request.incident_vin or "",
            "MARSHAL_PLANT_TIMEOUT": str(request.timeout_s),
            "MARSHAL_RECORD": "1" if request.record else "0",
        }
        async for item in self._stream(request.script, env):
            yield item

    async def stream_lab(self, request: BatchLabRequest) -> AsyncIterator[dict[str, Any]]:
        env = {
            **os.environ,
            "MARSHAL_LAB_REQUEST": request.situation,
            "MARSHAL_LAB_SOURCE": request.source,
            "MARSHAL_LAB_ADVERSARIAL": str(request.adversarial_budget),
            "MARSHAL_LAB_OUTPUT": request.output,
        }
        async for item in self._stream(request.script, env):
            yield item

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    async def terminate(self) -> None:
        if self._process is not None:
            await self._terminate_process(self._process)


_PROCESS_RUNNER = SubprocessBatchRunner()


class BatchService:
    def __init__(
        self,
        store: BatchStore,
        runner: BatchProcessRunner | None = None,
        tenant_id: str = "tenant-demo",
        *,
        llm: LLM | None = None,
        settings: Settings | None = None,
        llm_mode: str | None = None,
    ) -> None:
        self.store = store
        self.runner = runner or _PROCESS_RUNNER
        self.tenant_id = tenant_id
        self.llm = llm
        self.settings = settings or Settings()
        self.llm_mode = llm_mode or AgentSettings().llm
        self._task: asyncio.Task | None = None

    def _plant_id(self) -> str:
        return os.getenv("MARSHAL_PLANT_ID", "plant-demo")

    async def create_batch(self, cars: int, seed: int | None = None) -> BatchAccepted:
        if not self.settings.batch_enabled:
            raise ApiError(503, "batch_disabled", "CARLA batch runs are disabled")
        if not 1 <= cars <= self.settings.batch_max_cars:
            raise ApiError(422, "validation_error", f"cars must be between 1 and {self.settings.batch_max_cars}")
        batch_id = f"BAT-{generate_ulid()[:10]}"
        manifest = build_manifest(batch_id, cars, seed)
        now = utc_now_iso()
        batch = {
            "tenant_id": self.tenant_id,
            "plant_id": self._plant_id(),
            "batch_id": batch_id,
            "job_id": f"JOB-{generate_ulid()[:12]}",
            "status": "queued",
            "engine": "carla",
            "requested_cars": cars,
            "created_cars": cars,
            "finished_cars": 0,
            "progress": 0,
            "seed": manifest["seed"],
            "incident_enabled": manifest["incident"]["enabled"],
            "incident_vin": manifest["incident"]["vin"],
            "message": "queued",
            "manifest": manifest,
            "counts": default_flow_counts(),
            "result": {},
            "lab": {},
            "report": {},
            "error": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
        }
        car_rows = [{
            "vin": car["vin"],
            "truck_id": car["truck_id"],
            "expected": car["expected"],
            "state": "line_end",
            "stage": 1,
            "location": "line_end",
            "slot": None,
            "gate_verdict": None,
            "report_verdict": None,
            "actual": {},
            "updated_at": now,
        } for car in manifest["cars"]]
        if not await self.store.create(batch, car_rows):
            raise ApiError(409, "batch_running", "Another CARLA batch is already active")
        self._task = _spawn(self._run_batch(self.tenant_id, batch_id))
        return BatchAccepted(batch_id=batch_id, job_id=batch["job_id"], status="queued",
                             requested_cars=cars, created_cars=cars)

    async def list_batches(self, status: str | None = None, page: int = 1, limit: int = 20) -> Page[BatchSummary]:
        rows, total = await self.store.list(self.tenant_id, status, page, limit)
        return Page(items=[self._summary(row) for row in rows],
                    pagination=Pagination(page=page, limit=limit, total=total,
                                          last_page=max(1, -(-total // limit))))

    async def get_batch(self, batch_id: str) -> BatchDetail:
        batch = await self._get(batch_id)
        return BatchDetail(
            **self._summary(batch).model_dump(), message=batch.get("message"), seed=batch["seed"],
            incident_enabled=batch["incident_enabled"], incident_vin=batch.get("incident_vin"),
            job_id=batch["job_id"],
            flow_counts=BatchFlowCounts.model_validate(batch.get("counts") or default_flow_counts()),
            cars=[BatchCarDto(
                vin=row["vin"], truck_id=row.get("truck_id"), expected=row.get("expected", {}),
                state=row["state"], stage=row["stage"], location=row["location"], slot=row.get("slot"),
                gate_verdict=row.get("gate_verdict"), report_verdict=row.get("report_verdict"),
                updated_at=row["updated_at"],
            ) for row in batch.get("cars", [])],
            report=batch.get("report") or None,
        )

    async def get_events(self, batch_id: str, since: int = 0, limit: int = 200,
                         flow: int | None = None) -> BatchEvents:
        await self._get(batch_id)
        rows, has_more = await self.store.get_events(self.tenant_id, batch_id, since, limit, flow)
        return BatchEvents(items=[BatchEventDto(seq=row["seq"], flow=row.get("flow"),
                                                source=row["source"], title=row["title"],
                                                detail=row.get("detail", ""), level=row["level"],
                                                at=row["at"]) for row in rows],
                           next_since=rows[-1]["seq"] if rows else since, has_more=has_more)

    async def get_report(self, batch_id: str) -> BatchReport:
        batch = await self._get(batch_id)
        if batch["status"] != "done" or not batch.get("report"):
            raise ApiError(409, "report_not_ready", "Batch report is not ready")
        return BatchReport.model_validate(batch["report"])

    async def cancel(self, batch_id: str) -> BatchCancelAccepted:
        batch = await self._get(batch_id)
        if batch["status"] not in {"queued", "running"}:
            raise ApiError(409, "batch_not_running", "Batch is already finished")
        fields = {
            "status": "cancelled",
            "message": "cancelled by operator",
            "progress": min(int(batch.get("progress", 0)), 99),
            "finished_at": utc_now_iso(),
            "active_resource": None,
        }
        await self.store.update(self.tenant_id, batch_id, fields)
        await self.store.append_events(self.tenant_id, batch_id, [{
            "flow": None, "source": "Batch service", "title": "Batch cancelled",
            "detail": fields["message"], "level": "warn", "at": utc_now_iso(), "payload": {},
        }])
        await self.store.release(batch_id)
        await self.runner.terminate()
        return BatchCancelAccepted(batch_id=batch_id, status="cancelled")

    async def _get(self, batch_id: str) -> dict:
        batch = await self.store.get(self.tenant_id, batch_id)
        if batch is None:
            raise ApiError(404, "batch_not_found", f"Batch {batch_id} not found")
        return batch

    @staticmethod
    def _summary(batch: dict) -> BatchSummary:
        return BatchSummary(batch_id=batch["batch_id"], status=batch["status"],
                            engine=batch.get("engine", "carla"),
                            requested_cars=batch["requested_cars"],
                            created_cars=batch.get("created_cars", 0),
                            finished_cars=batch.get("finished_cars", 0),
                            progress=batch.get("progress", 0), created_at=batch["created_at"],
                            started_at=batch.get("started_at"), finished_at=batch.get("finished_at"))

    async def _persist(self, tenant_id: str, batch: dict) -> None:
        batch["updated_at"] = utc_now_iso()
        await self.store.update(tenant_id, batch["batch_id"],
                                {key: batch.get(key) for key in PERSISTED_FIELDS})

    async def _run_batch(self, tenant_id: str, batch_id: str) -> None:
        batch = await self.store.get(tenant_id, batch_id)
        if batch is None or batch["status"] != "queued":
            return
        batch.update({"status": "running", "started_at": utc_now_iso(), "progress": 5,
                      "message": "starting CARLA plant"})
        await self._persist(tenant_id, batch)

        buffer: list[dict] = []
        try:
            with tempfile.TemporaryDirectory(prefix="marshal-batch-") as tmp:
                manifest_path = Path(tmp) / "manifest.json"
                write_manifest(batch["manifest"], manifest_path)
                vins = [car["vin"] for car in batch["manifest"]["cars"]]
                prefix = vins[0].rsplit("-", 1)[0] + "-"
                request = BatchRunRequest(
                    batch_id=batch["batch_id"], manifest_path=str(manifest_path),
                    cars=batch["requested_cars"], vins=vins, prefix=prefix,
                    incident_vin=batch.get("incident_vin"),
                    script=self.settings.batch_plant_script,
                    timeout_s=self.settings.batch_timeout_s,
                )
                await asyncio.wait_for(self._consume(self.runner.stream(request), tenant_id, batch, buffer),
                                       timeout=self.settings.batch_timeout_s)
            await self._flush_events(tenant_id, batch, buffer)
            fresh = await self.store.get(tenant_id, batch_id)
            if fresh is None or fresh["status"] == "cancelled":
                return
            batch = fresh
            if not batch.get("result"):
                raise RuntimeError("CARLA plant exited without plant.done")
            if batch["result"].get("result") not in (None, "success"):
                raise RuntimeError("CARLA batch did not meet its completion checks")
            await self._run_lab_and_report(tenant_id, batch)
        except asyncio.TimeoutError:
            await self.runner.terminate()
            await self._fail(tenant_id, batch, f"batch timed out after {self.settings.batch_timeout_s} s")
        except asyncio.CancelledError:
            await self.runner.terminate()
            fresh = await self.store.get(tenant_id, batch_id)
            if fresh is not None and fresh["status"] not in TERMINAL:
                await self._terminal(tenant_id, fresh, "cancelled", "batch cancelled")
            raise
        except Exception as exc:
            await self.runner.terminate()
            fresh = await self.store.get(tenant_id, batch_id)
            if fresh is not None and fresh["status"] != "cancelled":
                if isinstance(self.runner, SubprocessBatchRunner) and self.runner.stderr_tail:
                    fresh["result"] = {**(fresh.get("result") or {}),
                                       "stderr_tail": list(self.runner.stderr_tail)}
                await self._fail(tenant_id, fresh, str(exc))
            log.exception("batch %s failed", batch_id)
        finally:
            await self.store.release(batch_id)

    async def _consume(self, stream, tenant_id: str, batch: dict, buffer: list[dict]) -> None:
        iterator = stream.__aiter__()
        last_flush = time.monotonic()
        next_line = asyncio.create_task(iterator.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {next_line}, timeout=max(0.05, 1.0 - (time.monotonic() - last_flush))
                )
                if not done:
                    await self._flush_events(tenant_id, batch, buffer)
                    last_flush = time.monotonic()
                    continue
                try:
                    line = next_line.result()
                except StopAsyncIteration:
                    break
                if isinstance(line, dict):
                    await self._handle_line(tenant_id, batch, line, buffer)
                    if len(buffer) >= 50 or time.monotonic() - last_flush >= 1.0:
                        await self._flush_events(tenant_id, batch, buffer)
                        last_flush = time.monotonic()
                next_line = asyncio.create_task(iterator.__anext__())
        finally:
            if not next_line.done():
                next_line.cancel()
                await asyncio.gather(next_line, return_exceptions=True)
        await self._flush_events(tenant_id, batch, buffer)

    async def _handle_line(self, tenant_id: str, batch: dict, line: dict, buffer: list[dict]) -> None:
        kind = line.get("type")
        counts = copy.deepcopy(batch.get("counts") or default_flow_counts())
        cars = batch.get("cars") or []
        if kind == "plant.start":
            batch["created_cars"] = len(line.get("cars", [])) or batch["requested_cars"]
            batch["progress"], batch["message"] = 10, "cars released to the plant"
        elif kind == "plant.car":
            vin = line.get("vin")
            for car in cars:
                if car["vin"] != vin:
                    continue
                car["state"] = str(line.get("state", car["state"]))
                car["location"] = str(line.get("location", car["location"]))
                car["slot"] = line.get("slot") or car.get("slot")
                car["stage"] = stage_for(car["state"], bool(line.get("open_case")))
                car["gate_verdict"] = line.get("gate_verdict") or car.get("gate_verdict")
                car["report_verdict"] = line.get("report_verdict") or car.get("report_verdict")
                car["actual"] = {**car.get("actual", {}), **line}
                car["updated_at"] = utc_now_iso()
                break
            batch["finished_cars"] = sum(
                car["state"] in {"yard", "shipped"}
                or (car["state"] == "rework" and (car["location"].startswith("rework.")
                                                  or car["location"] == "production_control"))
                for car in cars
            )
            counts["flow1"].update({
                "waiting": sum(car["state"] == "line_end" for car in cars),
                "released": sum(car["stage"] >= 3 for car in cars),
                "in_yard": sum(car["state"] == "yard" for car in cars),
                "shipped": sum(car["state"] == "shipped" for car in cars),
            })
            batch["progress"] = min(89, 10 + int(80 * batch["finished_cars"]
                                                 / max(batch["requested_cars"], 1)))
            batch["message"] = f"{batch['finished_cars']} of {batch['requested_cars']} cars finished"
        elif kind == "plant.progress":
            batch["message"] = "CARLA plant is running"
        elif kind in {"plant.supervisor", "batch.event", "plant.warning"}:
            flow = line.get("flow")
            if kind == "plant.supervisor":
                flow = 1 if line.get("decision") == "re-plan approved" else 5
            if kind == "plant.warning":
                flow = 2
            level = str(line.get("level", "warn" if kind == "plant.warning" else "info"))
            buffer.append({"flow": flow, "source": line.get("source", "Plant"),
                           "title": line.get("title") or line.get("decision") or line.get("code") or kind,
                           "detail": line.get("detail") or line.get("summary") or line.get("why") or "",
                           "level": level, "at": utc_now_iso(), "payload": line})
        elif kind in {"flow2.request", "flow2.card", "flow2.decided"}:
            flow2 = counts["flow2"]
            if kind == "flow2.request":
                flow2["cases"] += 1
                flow2["open"] += 1
            elif kind == "flow2.decided" and line.get("resolved", True):
                flow2["open"] = max(0, flow2["open"] - 1)
                flow2["resolved"] += 1
            buffer.append({"flow": 2, "source": "Assistance", "title": kind.replace(".", " "),
                           "detail": str(line.get("situation") or line.get("reason") or line.get("choice") or ""),
                           "level": "good" if kind == "flow2.decided" else "warn",
                           "at": utc_now_iso(), "payload": line})
        elif kind == "plant.done":
            batch["result"] = dict(line)
            actual_cars = line.get("cars", {})
            labels = self._label_outcomes(batch["manifest"], line)
            batch["result"]["labels"] = labels
            for car in cars:
                actual = actual_cars.get(car["vin"], {})
                car["actual"] = {**car.get("actual", {}), **actual,
                                 "label": next((label for label in labels["cars"]
                                                if label["vin"] == car["vin"]), {})}
                car["state"] = str(actual.get("state", car["state"]))
                car["location"] = str(actual.get("location", car["location"]))
                car["gate_verdict"] = actual.get("gate") or car.get("gate_verdict")
                car["report_verdict"] = actual.get("report") or car.get("report_verdict")
                car["stage"] = stage_for(car["state"])
                car["updated_at"] = utc_now_iso()
            batch["finished_cars"] = sum(is_finished(str(car.get("state", "")))
                                         for car in actual_cars.values())
            batch["progress"], batch["message"] = 90, "plant complete; preparing batch report"
            counts["flow1"]["shipped"] = sum(len(v) for v in line.get("shipped", {}).values()
                                             if isinstance(v, list))
            self._counts_from_result(counts, line)
        elif kind == "plant.error":
            batch["error"] = str(line.get("error", "CARLA plant failed"))[:500]
        elif kind == "plant.timeout":
            batch["error"] = f"CARLA plant timed out after {line.get('after_s', 'unknown')} s"
        elif kind and kind.startswith("lab."):
            self._handle_lab_event(counts, batch, line)
            buffer.append({"flow": 7 if "adversarial" in kind else 6, "source": "CARLA test lab",
                           "title": kind,
                           "detail": json.dumps({k: v for k, v in line.items() if k != "type"},
                                                default=str)[:500],
                           "level": "bad" if line.get("status") == "failed" else "info",
                           "at": utc_now_iso(), "payload": line})

        batch["counts"] = counts
        await self._persist(tenant_id, batch)

    @staticmethod
    def _counts_from_result(counts: dict, result: dict) -> None:
        cars = result.get("cars", {})
        gate = [car.get("gate") for car in cars.values()]
        reports = [car.get("report") for car in cars.values()]
        functional = result.get("functional", {})
        functional_verdicts = [v.get("verdict") for v in functional.values() if isinstance(v, dict)]
        counts["flow3"] = {"pass": gate.count("PASS"), "review": gate.count("REVIEW"),
                           "fail": gate.count("FAIL")}
        counts["flow4"] = {"pass": functional_verdicts.count("PASS"),
                           "fail": functional_verdicts.count("FAIL"),
                           "disagreements": sum(bool(v.get("disagreements"))
                                                for v in functional.values() if isinstance(v, dict))}
        counts["flow5"] = {"pass": reports.count("PASS"), "rework": reports.count("FAIL"),
                           "shipped": sum(len(v) for v in result.get("shipped", {}).values()
                                          if isinstance(v, list))}
        incident = result.get("incident", {})
        counts["flow2"].update({"cases": int(bool(incident.get("case_id"))),
                                "open": 0 if incident.get("resolved") else int(bool(incident.get("case_id"))),
                                "resolved": int(bool(incident.get("resolved"))), "escalated": 0})

    @staticmethod
    def _label_outcomes(manifest: dict, result: dict) -> dict:
        results = result.get("cars", {})
        functional = result.get("functional", {})
        incident = result.get("incident", {})
        rows = []
        caught = missed = false_alarms = 0
        for car in manifest.get("cars", []):
            vin = car["vin"]
            expected = car.get("expected", {}).get("kind", "clean")
            actual = results.get(vin, {})
            gate = actual.get("gate")
            mismatch = " ".join(actual.get("mismatches", [])).lower()
            if expected == "wrong_colour":
                found = gate == "FAIL" and "colour" in mismatch
            elif expected == "dead_lamp":
                steps = functional.get(vin, {}).get("checklist", [])
                fault = car.get("expected", {}).get("fault_step")
                found = any(step.get("name") == fault and step.get("telemetry_ok") is True
                            and step.get("camera_ok") is False for step in steps)
            elif expected == "incident":
                found = bool(incident.get("resolved") and incident.get("test_requests"))
            else:
                found = None
                false_alarms += int(gate in ("REVIEW", "FAIL"))
            if expected != "clean":
                caught += int(bool(found))
                missed += int(not found)
            rows.append({"vin": vin, "expected": expected, "caught": found,
                         "evidence": {"gate": gate, "mismatch": mismatch or None,
                                      "functional": functional.get(vin, {}).get("verdict")}})
        return {"total": len(rows), "caught": caught, "missed": missed,
                "false_alarms": false_alarms, "cars": rows}

    @staticmethod
    def _handle_lab_event(counts: dict, batch: dict, line: dict) -> None:
        lab = copy.deepcopy(batch.get("lab") or {"status": "running"})
        kind = line.get("type")
        if kind == "lab.generated":
            lab.update({"scenario_id": line.get("scenario_id"),
                        "generated": {k: v for k, v in line.items() if k != "type"}})
            counts["flow6"].update({"generated": 1, "status": "generated"})
        elif kind == "lab.judged":
            lab["judged"] = {k: v for k, v in line.items() if k != "type"}
        elif kind == "lab.reviewed":
            lab["reviewed"] = {k: v for k, v in line.items() if k != "type"}
        elif kind == "lab.adversarial.done":
            lab["adversarial"] = {k: v for k, v in line.items() if k != "type"}
            counts["flow7"].update({"runs": int(line.get("runs", 0)),
                                    "failures": int(not line.get("robust", False)), "status": "done"})
        elif kind == "lab.error":
            lab["error"] = str(line.get("error", "lab failed"))[:300]
            counts["flow6"]["status"], counts["flow7"]["status"] = "failed", "skipped"
        lab["status"] = "failed" if kind == "lab.error" else "running"
        batch["lab"] = lab

    async def _run_lab_and_report(self, tenant_id: str, batch: dict) -> None:
        result = batch.get("result") or {}
        incident = result.get("incident", {})
        requests = incident.get("test_requests", [])
        if batch["incident_enabled"] and requests:
            situation = str(requests[0].get("text") or batch["manifest"]["incident"]["situation"])
            source = str(requests[0].get("source") or f"assistance case {incident.get('case_id', '')}")
            # shortcut: lab artifacts stay on this host; move them to GCS if cross-host retention is required.
            lab_dir = f"/tmp/marshal-batch-{batch['batch_id']}-lab"
            request = BatchLabRequest(batch_id=batch["batch_id"], situation=situation, source=source,
                                      output=lab_dir, script=self.settings.batch_lab_script,
                                      adversarial_budget=self.settings.batch_lab_adversarial,
                                      timeout_s=self.settings.batch_lab_timeout_s)
            batch["counts"]["flow6"]["test_requests"] = len(requests)
            batch["counts"]["flow6"]["status"] = "running"
            await self._persist(tenant_id, batch)
            try:
                await asyncio.wait_for(self._consume_lab(self.runner.stream_lab(request), tenant_id, batch),
                                       timeout=self.settings.batch_lab_timeout_s)
                if not (batch.get("lab") or {}).get("adversarial"):
                    raise RuntimeError("lab exited without adversarial result")
                batch["lab"].update({"status": "done", "artifacts_path": lab_dir})
            except asyncio.TimeoutError:
                await self.runner.terminate()
                batch["lab"] = {**(batch.get("lab") or {}), "status": "failed", "error": "lab timed out"}
                batch["counts"]["flow6"]["status"] = "failed"
                batch["counts"]["flow7"]["status"] = "skipped"
            except Exception as exc:
                batch["lab"] = {**(batch.get("lab") or {}), "status": "failed", "error": str(exc)[:300]}
                batch["counts"]["flow6"]["status"] = "failed"
                batch["counts"]["flow7"]["status"] = "skipped"
            await self._persist(tenant_id, batch)
        else:
            batch["lab"] = {"status": "not_triggered"}
            batch["counts"]["flow6"].update({"status": "not_triggered", "test_requests": 0, "generated": 0})
            batch["counts"]["flow7"].update({"status": "not_triggered", "runs": 0, "failures": 0})

        fresh = await self.store.get(tenant_id, batch["batch_id"])
        if fresh is None or fresh["status"] == "cancelled":
            return
        batch = fresh
        car_data = [{"vin": car["vin"], "state": car["state"], "location": car["location"],
                     "stage": car["stage"], "expected": car.get("expected", {}),
                     "actual": car.get("actual", {}), "gate_verdict": car.get("gate_verdict"),
                     "report_verdict": car.get("report_verdict")} for car in batch.get("cars", [])]
        event_rows, _ = await self.store.get_events(tenant_id, batch["batch_id"], 0, 1000, None)
        event_data = [{"seq": row["seq"], "flow": row.get("flow"), "source": row["source"],
                       "title": row["title"], "detail": row.get("detail", ""),
                       "level": row["level"]} for row in event_rows]
        batch_data = {
            "batch_id": batch["batch_id"], "requested_cars": batch["requested_cars"],
            "created_cars": batch["created_cars"], "finished_cars": batch.get("finished_cars", 0),
            "engine": batch.get("engine", "carla"), "incident_enabled": batch["incident_enabled"],
            "counts": batch["counts"], "flow_counts": batch["counts"],
            "started_at": batch.get("started_at"), "finished_at": utc_now_iso(),
        }
        digest = build_digest(batch_data, car_data, event_data, batch.get("result") or {},
                              batch.get("lab") or {})
        feedback_dict = offline_feedback(digest)
        degraded = self.llm_mode == "offline" or self.llm is None
        feedback_error = None
        if self.llm is not None:
            try:
                raw = await asyncio.wait_for(asyncio.to_thread(
                    self.llm.generate, "batch.feedback", build_prompt(digest), BatchFeedback,
                    None, SYSTEM,
                ), timeout=60)
                feedback_dict = normalize_feedback(raw, batch["created_cars"]).model_dump()
            except Exception as exc:
                degraded = True
                feedback_error = str(exc)[:240]
        feedback_dict = normalize_feedback(feedback_dict, batch["created_cars"]).model_dump()
        feedback_dict["degraded"] = degraded
        if feedback_error:
            feedback_dict["error"] = feedback_error
        batch["finished_at"] = utc_now_iso()
        batch_data["finished_at"] = batch["finished_at"]
        report = build_report(batch_data, car_data, event_data, batch.get("result") or {},
                              batch.get("lab") or {}, feedback_dict, model=self.settings.model)
        batch.update({"report": report, "status": "done", "active_resource": None,
                      "progress": 100, "message": "batch report ready"})
        await self._persist(tenant_id, batch)

    async def _consume_lab(self, stream, tenant_id: str, batch: dict) -> None:
        async for line in stream:
            if not isinstance(line, dict):
                continue
            counts = copy.deepcopy(batch.get("counts") or default_flow_counts())
            self._handle_lab_event(counts, batch, line)
            batch["counts"] = counts
            await self._persist(tenant_id, batch)

    async def _flush_events(self, tenant_id: str, batch: dict, buffer: list[dict]) -> None:
        if not buffer:
            return
        await self.store.append_events(tenant_id, batch["batch_id"], buffer)
        buffer.clear()

    async def _terminal(self, tenant_id: str, batch: dict, status: str, message: str) -> None:
        batch.update({"status": status, "message": message, "finished_at": utc_now_iso(),
                      "active_resource": None})
        await self._persist(tenant_id, batch)
        await self.store.release(batch["batch_id"])

    async def _fail(self, tenant_id: str, batch: dict, error: str) -> None:
        batch["error"] = error[:500]
        if isinstance(self.runner, SubprocessBatchRunner) and self.runner.stderr_tail:
            batch["result"] = {**(batch.get("result") or {}),
                               "stderr_tail": list(self.runner.stderr_tail)}
        await self._terminal(tenant_id, batch, "failed", "batch failed")
        await self.store.append_events(tenant_id, batch["batch_id"], [{
            "flow": None, "source": "Batch service", "title": "Batch failed",
            "detail": error[:500], "level": "bad", "at": utc_now_iso(), "payload": {},
        }])


async def mark_stale_batches(store: BatchStore) -> None:
    """A process restart cannot resume a live CARLA stream; make its rows visibly terminal."""
    for batch in await store.list_active():
        fields = {
            "status": "failed",
            "error": "API restarted before the batch finished",
            "message": "batch interrupted",
            "finished_at": utc_now_iso(),
            "active_resource": None,
        }
        await store.update(batch["tenant_id"], batch["batch_id"], fields)
        await store.release(batch["batch_id"])


async def shutdown_batches(store: BatchStore) -> None:
    for batch in await store.list_active():
        fields = {
            "status": "cancelled",
            "message": "API shutdown",
            "finished_at": utc_now_iso(),
            "active_resource": None,
        }
        await store.update(batch["tenant_id"], batch["batch_id"], fields)
        await store.release(batch["batch_id"])
    await _PROCESS_RUNNER.terminate()


def get_batch_process_runner() -> BatchProcessRunner:
    return _PROCESS_RUNNER
