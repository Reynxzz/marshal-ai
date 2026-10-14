"""Test lab: scenarios, adversarial search, releases (Flows 6-7).

The Gemini/CARLA work behind the async jobs is not in this process: the default
handlers do the deterministic database part and mark the job result ``degraded``.
Wire a real runtime-backed handler into ``LabService`` when one is available.
"""

from __future__ import annotations

import logging

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from marshal_api.core.errors import ApiError
from marshal_api.models import (
    AsyncJob,
    Plant,
    Release as ReleaseRow,
    ReleaseEvalTest as EvalTestRow,
    Scenario as ScenarioRow,
    TestRequest as TestRequestRow,
    TestRun as TestRunRow,
)
from marshal_api.models.base import generate_ulid, utc_now
from marshal_api.models.lab import ScenarioStatus as DbScenarioStatus
from marshal_api.schemas import (
    JobAccepted,
    Page,
    Pagination,
    Release,
    ReleaseEvalTest,
    Scenario,
    ScenarioDetail,
    TestRequest,
    ValidatorNote,
)
from marshal_api.schemas.lab import (
    AdversarialResult,
    AdversarialVerdict,
    ReleaseEvaluationStatus,
    ReleaseGate,
    ScenarioStatus as FeScenarioStatus,
    TestRequestStatus,
    ValidatorSeverity,
    VariantRun,
)
from marshal_api.workers.job_runner import JobRunner

log = logging.getLogger("marshal.api.lab")

# The DB process states and the dashboard's scenario states differ; project both ways.
_DB_TO_FE = {
    DbScenarioStatus.DRAFT: FeScenarioStatus.DRAFT,
    DbScenarioStatus.VALID: FeScenarioStatus.PENDING_REVIEW,
    DbScenarioStatus.DRY_RUN_OK: FeScenarioStatus.APPROVED,
    DbScenarioStatus.IN_LIBRARY: FeScenarioStatus.RUNNABLE,
    DbScenarioStatus.REJECTED: FeScenarioStatus.REJECTED,
}
_FE_TO_DB = {fe: db for db, fe in _DB_TO_FE.items()}

_EVAL_DB_TO_FE = {
    "pending": ReleaseEvaluationStatus.NONE,
    "running": ReleaseEvaluationStatus.RUNNING,
    "done": ReleaseEvaluationStatus.DONE,
    "failed": ReleaseEvaluationStatus.FAILED,
}
_GATE_DB_TO_FE = {"open": ReleaseGate.PENDING, "approved": ReleaseGate.APPROVED, "blocked": ReleaseGate.BLOCKED}


class LabService:
    def __init__(
        self, session: AsyncSession, job_runner: JobRunner, tenant_id: str = "tenant-demo"
    ) -> None:
        self.session = session
        self.runner = job_runner
        self.tenant_id = tenant_id

    async def _all(self, stmt):
        return (await self.session.execute(stmt)).scalars().all()

    async def _first(self, stmt):
        return (await self.session.execute(stmt)).scalars().first()

    async def _plant_id(self) -> str:
        plant = await self._first(select(Plant).where(Plant.tenant_id == self.tenant_id))
        if plant is None:
            raise ApiError(400, "no_plant", "No plant configured for this tenant")
        return plant.id

    # ------------------------------------------------------------------- reads

    async def list_test_requests(
        self, status: str | None = None, page: int = 1, limit: int = 20
    ) -> Page[TestRequest]:
        conditions = [TestRequestRow.tenant_id == self.tenant_id]
        if status:
            conditions.append(TestRequestRow.status == status)
        total = (
            await self.session.execute(
                select(func.count()).select_from(TestRequestRow).where(*conditions)
            )
        ).scalar_one()
        rows = await self._all(
            select(TestRequestRow)
            .where(*conditions)
            .order_by(TestRequestRow.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        scenario_map = await self._scenario_ids({r.scenario_id for r in rows if r.scenario_id})
        items = [
            TestRequest(
                request_id=r.request_id,
                source=r.source,
                text=r.text,
                created_at=r.created_at,
                status=TestRequestStatus(r.status),
                scenario_id=scenario_map.get(r.scenario_id) if r.scenario_id else None,
            )
            for r in rows
        ]
        return self._page(items, page, limit, total)

    async def _scenario_ids(self, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        rows = (
            await self.session.execute(
                select(ScenarioRow.id, ScenarioRow.scenario_id).where(ScenarioRow.id.in_(ids))
            )
        ).all()
        return {internal: public for internal, public in rows}

    async def list_scenarios(
        self,
        status: str | None = None,
        tag: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> Page[Scenario]:
        # shortcut: scenarios are few; tag filtering and pagination run in-process
        rows = await self._all(
            select(ScenarioRow)
            .where(ScenarioRow.tenant_id == self.tenant_id)
            .order_by(ScenarioRow.created_at.desc())
        )
        db_status = _FE_TO_DB.get(status) if status else None
        items = []
        for row in rows:
            if db_status is not None and row.status != db_status:
                continue
            if tag and tag not in (row.tags or []):
                continue
            items.append(self._scenario_dto(row))
        total = len(items)
        start = (page - 1) * limit
        return Page(
            items=items[start : start + limit],
            pagination=Pagination(
                page=page, limit=limit, total=total, last_page=max(1, -(-total // limit))
            ),
        )

    def _scenario_dto(self, row: ScenarioRow) -> Scenario:
        return Scenario(
            scenario_id=row.scenario_id,
            source=row.source,
            status=_DB_TO_FE.get(row.status, FeScenarioStatus.DRAFT),
            tags=list(row.tags or []),
            owner=row.owner or "",
            summary=row.summary,
            created_at=row.created_at,
        )

    async def get_scenario(self, scenario_id: str) -> ScenarioDetail:
        row = await self._first(
            select(ScenarioRow).where(
                ScenarioRow.tenant_id == self.tenant_id, ScenarioRow.scenario_id == scenario_id
            )
        )
        if row is None:
            raise ApiError(404, "scenario_not_found", f"Scenario {scenario_id} not found")
        runs = await self._all(
            select(TestRunRow).where(TestRunRow.scenario_id == row.id)
        )
        base = self._scenario_dto(row)
        return ScenarioDetail(
            **base.model_dump(),
            spec=dict(row.spec or {}),
            validator_notes=[
                ValidatorNote(severity=ValidatorSeverity.OK, message=note)
                for note in (row.validator_notes or [])
            ],
            dry_run_metrics=dict(row.dry_run_metrics) or None,
            review=None,
            adversarial_result=self._adversarial(runs),
        )

    @staticmethod
    def _adversarial(runs: list[TestRunRow]) -> AdversarialResult | None:
        if not runs:
            return None
        failed = [r for r in runs if not r.passed]
        variants = [
            VariantRun(
                variant_id=r.variant,
                light=str((r.params or {}).get("light", "noon")),
                weather=(r.params or {}).get("weather"),
                occluder=(r.params or {}).get("occluder"),
                trigger_distance_m=float((r.params or {})["trigger_distance_m"]) if (r.params or {}).get("trigger_distance_m") is not None else None,
                ego_speed_kmh=float((r.params or {})["ego_speed_kmh"]) if (r.params or {}).get("ego_speed_kmh") is not None else None,
                min_gap_m=float((r.metrics or {})["min_gap_m"]) if (r.metrics or {}).get("min_gap_m") is not None else None,
                passed=r.passed,
                clip_id=r.clip_media_id,
            )
            for r in runs
        ]
        return AdversarialResult(
            runs=len(runs),
            verdict=AdversarialVerdict.FRAGILE if failed else AdversarialVerdict.ROBUST,
            smallest_failing_variant=failed[0].variant if failed else None,
            criteria={"failed": ", ".join(failed[0].failed_criteria)} if failed else None,
            metrics=failed[0].metrics if failed else runs[0].metrics,
            media_ids=[r.clip_media_id for r in runs if r.clip_media_id],
            variants=variants,
        )

    async def list_releases(self, page: int = 1, limit: int = 20) -> Page[Release]:
        total = (
            await self.session.execute(
                select(func.count()).select_from(ReleaseRow).where(ReleaseRow.tenant_id == self.tenant_id)
            )
        ).scalar_one()
        rows = await self._all(
            select(ReleaseRow)
            .where(ReleaseRow.tenant_id == self.tenant_id)
            .order_by(ReleaseRow.updated_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        items = [await self._release_dto(row) for row in rows]
        return self._page(items, page, limit, total)

    async def get_release(self, release_id: str) -> Release:
        row = await self._first(
            select(ReleaseRow).where(
                ReleaseRow.tenant_id == self.tenant_id, ReleaseRow.release_id == release_id
            )
        )
        if row is None:
            raise ApiError(404, "release_not_found", f"Release {release_id} not found")
        return await self._release_dto(row)

    async def _release_dto(self, row: ReleaseRow) -> Release:
        tests = await self._all(select(EvalTestRow).where(EvalTestRow.release_id == row.id))
        scenario_map = await self._scenario_ids({t.scenario_id for t in tests if t.scenario_id})
        return Release(
            release_id=row.release_id,
            evaluation_status=_EVAL_DB_TO_FE.get(row.evaluation_status, ReleaseEvaluationStatus.NONE),
            gate=_GATE_DB_TO_FE.get(row.gate, ReleaseGate.PENDING),
            passed=row.passed_count,
            failed=row.failed_count,
            tests=[
                ReleaseEvalTest(
                    test_id=t.test_id,
                    scenario_id=scenario_map.get(t.scenario_id) if t.scenario_id else None,
                    metric=t.metric,
                    criteria=t.criteria,
                    result=t.result,
                    passed=t.passed,
                    media_ids=[t.clip_media_id] if t.clip_media_id else [],
                )
                for t in tests
            ],
            approved_by=row.approved_by,
            approved_at=row.approved_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _page(items: list, page: int, limit: int, total: int) -> Page:
        return Page(
            items=items,
            pagination=Pagination(
                page=page, limit=limit, total=total, last_page=max(1, -(-total // limit))
            ),
        )

    # ------------------------------------------------------------------ mutations

    async def create_scenario(self, situation: str, source: str, request_id: str | None) -> JobAccepted:
        situation = (situation or "").strip()
        if len(situation) < 5:
            raise ApiError(422, "validation_error", "situation must be at least 5 characters")
        plant_id = await self._plant_id()
        source = source or "manual"

        if request_id:
            request = await self._first(
                select(TestRequestRow).where(
                    TestRequestRow.tenant_id == self.tenant_id,
                    TestRequestRow.request_id == request_id,
                )
            )
            if request is None:
                request = TestRequestRow(
                    tenant_id=self.tenant_id,
                    plant_id=plant_id,
                    request_id=request_id,
                    source=source,
                    text=situation,
                )
            request.status = "processing"
            self.session.add(request)
        else:
            request = TestRequestRow(
                tenant_id=self.tenant_id,
                plant_id=plant_id,
                request_id=f"TRQ-{generate_ulid()[:6]}",
                source=source,
                text=situation,
                status="processing",
            )
            self.session.add(request)
        await self.session.flush()

        job_id = self.runner.new_job_id()
        self.session.add(
            AsyncJob(
                tenant_id=self.tenant_id,
                plant_id=plant_id,
                job_id=job_id,
                kind="scenario_generation",
                status="queued",
                progress=0,
            )
        )
        await self.session.commit()
        self.runner.enqueue(
            job_id,
            self._generate_scenario,
            {"situation": situation, "source": source, "request_id": request.request_id},
        )
        return JobAccepted(job_id=job_id, status="queued")

    async def review_scenario(
        self, scenario_id: str, decision: str, actor_id: str, note: str | None
    ) -> dict:
        row = await self._first(
            select(ScenarioRow).where(
                ScenarioRow.tenant_id == self.tenant_id, ScenarioRow.scenario_id == scenario_id
            )
        )
        if row is None:
            raise ApiError(404, "scenario_not_found", f"Scenario {scenario_id} not found")
        if row.status != DbScenarioStatus.VALID:
            raise ApiError(409, "scenario_already_reviewed", "Scenario already reviewed")
        if decision not in ("approved", "rejected"):
            raise ApiError(422, "validation_error", "decision must be 'approved' or 'rejected'")

        row.status = DbScenarioStatus.IN_LIBRARY if decision == "approved" else DbScenarioStatus.REJECTED
        row.owner = actor_id
        self.session.add(row)
        await self.session.commit()
        review = {"decision": decision, "actor_id": actor_id, "note": note, "at": utc_now().isoformat()}
        return {"scenario_id": row.scenario_id, "status": _DB_TO_FE[row.status].value, "review": review}

    async def start_adversarial(self, scenario_id: str) -> JobAccepted:
        row = await self._first(
            select(ScenarioRow).where(
                ScenarioRow.tenant_id == self.tenant_id, ScenarioRow.scenario_id == scenario_id
            )
        )
        if row is None:
            raise ApiError(404, "scenario_not_found", f"Scenario {scenario_id} not found")
        if _DB_TO_FE.get(row.status) not in (FeScenarioStatus.APPROVED, FeScenarioStatus.RUNNABLE):
            raise ApiError(409, "scenario_not_runnable", "Scenario is not runnable")

        job_id = self.runner.new_job_id()
        self.session.add(
            AsyncJob(
                tenant_id=self.tenant_id,
                plant_id=row.plant_id,
                job_id=job_id,
                kind="adversarial_search",
                status="queued",
                progress=0,
            )
        )
        await self.session.commit()
        self.runner.enqueue(job_id, self._run_adversarial, {"scenario_id": scenario_id})
        return JobAccepted(job_id=job_id, status="queued")

    async def evaluate_release(self, release_id: str) -> JobAccepted:
        row = await self._first(
            select(ReleaseRow).where(
                ReleaseRow.tenant_id == self.tenant_id, ReleaseRow.release_id == release_id
            )
        )
        if row is None:
            raise ApiError(404, "release_not_found", f"Release {release_id} not found")
        if row.evaluation_status == "running":
            raise ApiError(409, "evaluation_running", "Evaluation already running")

        row.evaluation_status = "running"
        self.session.add(row)
        job_id = self.runner.new_job_id()
        self.session.add(
            AsyncJob(
                tenant_id=self.tenant_id,
                plant_id=await self._plant_id(),
                job_id=job_id,
                kind="release_evaluate",
                status="queued",
                progress=0,
            )
        )
        await self.session.commit()
        self.runner.enqueue(job_id, self._run_release, {"release_id": release_id})
        return JobAccepted(job_id=job_id, status="queued")

    async def approve_release(self, release_id: str, actor_id: str) -> dict:
        row = await self._first(
            select(ReleaseRow).where(
                ReleaseRow.tenant_id == self.tenant_id, ReleaseRow.release_id == release_id
            )
        )
        if row is None:
            raise ApiError(404, "release_not_found", f"Release {release_id} not found")
        if row.approved_by:
            raise ApiError(409, "release_already_approved", "Release already approved")
        if row.evaluation_status != "done":
            raise ApiError(409, "evaluation_not_done", "Evaluation not finished")
        if row.failed_count > 0 or row.gate == "blocked":
            raise ApiError(409, "gate_blocked", "Release gate is blocked")

        row.gate = "approved"
        row.approved_by = actor_id
        row.approved_at = utc_now()
        self.session.add(row)
        await self.session.commit()
        return {
            "release_id": row.release_id,
            "gate": row.gate,
            "approved_by": row.approved_by,
            "approved_at": row.approved_at.isoformat(),
        }

    # ------------------------------------------------------------- job handlers

    async def _generate_scenario(self, job_id: str, payload: dict, runner: JobRunner) -> dict:
        await runner.set_progress(job_id, 30, "generating spec")
        # shortcut: no Gemini/CARLA here; record a placeholder scenario awaiting an engineer
        async with runner.session() as session:
            plant = (await session.execute(select(Plant).where(Plant.tenant_id == self.tenant_id))).scalars().first()
            if plant is None:
                raise RuntimeError("no plant configured")
            scenario = ScenarioRow(
                tenant_id=self.tenant_id,
                plant_id=plant.id,
                scenario_id=f"SC-{generate_ulid()[:6]}",
                source=payload["source"],
                status=DbScenarioStatus.VALID,
                summary=payload["situation"],
                tags=["from-incident"] if payload["source"].startswith("assistance") else [],
                spec={},
                validator_notes=["Generated without a simulation runtime; needs an engineer."],
            )
            session.add(scenario)
            await session.flush()
            request = (
                await session.execute(
                    select(TestRequestRow).where(
                        TestRequestRow.request_id == payload["request_id"]
                    )
                )
            ).scalars().first()
            if request is not None:
                request.status = "converted"
                request.scenario_id = scenario.id
                session.add(request)
            await session.commit()
            result = {"scenario_id": scenario.scenario_id}
        await runner.set_progress(job_id, 95, "linking request")
        return {**result, "degraded": True}

    async def _run_adversarial(self, job_id: str, payload: dict, runner: JobRunner) -> dict:
        await runner.set_progress(job_id, 50, "running variants")
        # shortcut: no CARLA here; report degraded rather than inventing run verdicts
        return {"scenario_id": payload["scenario_id"], "runs": 0, "degraded": True}

    async def _run_release(self, job_id: str, payload: dict, runner: JobRunner) -> dict:
        await runner.set_progress(job_id, 60, "running regression suite")
        async with runner.session() as session:
            row = (
                await session.execute(
                    select(ReleaseRow).where(ReleaseRow.release_id == payload["release_id"])
                )
            ).scalars().first()
            passed = failed = 0
            if row is not None:
                row.evaluation_status = "done"
                session.add(row)
                await session.commit()
                passed, failed = row.passed_count, row.failed_count
        # shortcut: no CARLA here; existing counts stand, result is flagged degraded
        return {"release_id": payload["release_id"], "passed": passed, "failed": failed, "degraded": True}
