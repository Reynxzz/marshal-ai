"""Inspections, reports and the rework loop (Flows 3-5)."""

from __future__ import annotations

import logging

from sqlalchemy import func, or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from marshal_ai.driving import MissionClient, MissionGo
from marshal_ai.events import EventBus, Topic
from marshal_ai.plant.state import PlantState

from marshal_api.core.errors import ApiError
from marshal_api.models import (
    BuildsheetMismatch as MismatchRow,
    Car,
    CarReport as ReportRow,
    CarState,
    FunctionalStepCheck as StepRow,
    InspectionResult as ResultRow,
    ReworkBay as BayRow,
    ReworkTicket as TicketRow,
    VisualFinding as FindingRow,
)
from marshal_api.models.base import generate_ulid, utc_now
from marshal_api.schemas import (
    BuildSheetMismatch,
    FunctionalInspection,
    FunctionalStep,
    InspectionKpis,
    InspectionResult,
    Page,
    Pagination,
    Report,
    ReworkBayList,
    ReworkTicket,
    VisualFinding,
    VisualInspection,
)
from marshal_api.schemas.common import ReviewStatus, Verdict
from marshal_api.schemas.quality import InspectionKind, ReinspectionStatus, ReworkTicketStatus
from marshal_api.services.yard_service import rework_bay_dto

log = logging.getLogger("marshal.api.quality")


def _review_status(value: str | None) -> ReviewStatus:
    try:
        return ReviewStatus((value or "pending").upper())
    except ValueError:
        return ReviewStatus.PENDING


def _reinspection_status(value: str | None) -> ReinspectionStatus:
    try:
        return ReinspectionStatus((value or "not_required").upper())
    except ValueError:
        return ReinspectionStatus.NOT_REQUIRED


def _ticket_status(value: str) -> ReworkTicketStatus:
    try:
        return ReworkTicketStatus(value.upper())
    except ValueError:
        return ReworkTicketStatus.OPEN


def _finding_dto(f: FindingRow) -> VisualFinding:
    return VisualFinding(
        zone=f.zone,
        defect_type=f.defect_type,
        size=f"{f.size_cm:g} cm" if f.size_cm is not None else None,
        confidence=f.confidence,
        observation=f.observation or "",
        media_id=f.media_id,
    )


def _mismatch_dto(m: MismatchRow) -> BuildSheetMismatch:
    return BuildSheetMismatch(
        attribute=m.attribute, expected=m.expected, found=m.found, confidence=m.confidence
    )


def _step_ok(value: bool | None) -> str | None:
    return None if value is None else ("ok" if value else "fail")


def _step_dto(s: StepRow) -> FunctionalStep:
    return FunctionalStep(
        step=s.step_number,
        name=s.name,
        telemetry=_step_ok(s.telemetry_ok),
        camera_result=_step_ok(s.camera_ok),
        confidence=s.camera_confidence or 0.0,
        observation=s.observation,
        disagree=s.disagree,
        media_id=s.media_id,
    )


def _is_failed_step(s: StepRow) -> bool:
    return s.telemetry_ok is False or s.disagree


class QualityService:
    def __init__(
        self,
        session: AsyncSession,
        mission_client: MissionClient | None = None,
        event_bus: EventBus | None = None,
        plant_state: PlantState | None = None,
        tenant_id: str = "tenant-demo",
    ) -> None:
        self.session = session
        self.mission_client = mission_client
        self.event_bus = event_bus
        self.plant_state = plant_state
        self.tenant_id = tenant_id

    # ------------------------------------------------------------------ helpers

    async def _all(self, stmt):
        return (await self.session.execute(stmt)).scalars().all()

    async def _first(self, stmt):
        return (await self.session.execute(stmt)).scalars().first()

    async def _vins(self, car_ids: set[str]) -> dict[str, str]:
        if not car_ids:
            return {}
        rows = (await self.session.execute(select(Car.id, Car.vin).where(Car.id.in_(car_ids)))).all()
        return {car_id: vin for car_id, vin in rows}

    async def _cars_by_ids(self, car_ids: set[str]) -> dict[str, Car]:
        if not car_ids:
            return {}
        rows = await self._all(select(Car).where(Car.id.in_(car_ids)))
        return {car.id: car for car in rows}

    async def _car_by_vin(self, vin: str) -> Car | None:
        return await self._first(
            select(Car).where(Car.tenant_id == self.tenant_id, Car.vin == vin)
        )

    async def _latest_result(self, car_id: str, kind: str) -> ResultRow | None:
        return await self._first(
            select(ResultRow)
            .where(ResultRow.car_id == car_id, ResultRow.kind == kind)
            .order_by(ResultRow.created_at.desc())
        )

    # ------------------------------------------------------------------- reads

    async def list_results(
        self,
        kind: str | None = None,
        verdict: str | None = None,
        review_status: str | None = None,
        vin: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> Page[InspectionResult]:
        conditions = [ResultRow.tenant_id == self.tenant_id]
        if kind:
            conditions.append(ResultRow.kind == kind)
        if verdict:
            try:
                conditions.append(ResultRow.verdict == Verdict(verdict))
            except ValueError:
                raise ApiError(400, "invalid_verdict", f"Unknown verdict '{verdict}'")
        if review_status:
            conditions.append(ResultRow.review_status == review_status.lower())

        base = select(ResultRow).join(Car, Car.id == ResultRow.car_id).where(*conditions)
        if vin:
            base = base.where(Car.vin.ilike(f"%{vin}%"))
        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = await self._all(
            base.order_by(ResultRow.created_at.desc()).offset((page - 1) * limit).limit(limit)
        )

        ids = [r.id for r in rows]
        visual_counts: dict[str, int] = {}
        functional_counts: dict[str, int] = {}
        if ids:
            visual_counts = dict(
                (
                    await self.session.execute(
                        select(FindingRow.inspection_result_id, func.count())
                        .where(FindingRow.inspection_result_id.in_(ids))
                        .group_by(FindingRow.inspection_result_id)
                    )
                ).all()
            )
            functional_counts = dict(
                (
                    await self.session.execute(
                        select(StepRow.inspection_result_id, func.count())
                        .where(
                            StepRow.inspection_result_id.in_(ids),
                            or_(StepRow.telemetry_ok.is_(False), StepRow.disagree.is_(True)),
                        )
                        .group_by(StepRow.inspection_result_id)
                    )
                ).all()
            )

        vins = await self._vins({r.car_id for r in rows})
        items = [
            InspectionResult(
                result_id=r.result_id,
                vin=vins.get(r.car_id, ""),
                kind=InspectionKind(r.kind),
                verdict=r.verdict,
                finding_count=visual_counts.get(r.id, 0),
                failed_check_count=functional_counts.get(r.id, 0),
                at=r.created_at,
                review_status=_review_status(r.review_status),
                proposal_id=r.proposal_id,
            )
            for r in rows
        ]
        return Page(
            items=items,
            pagination=Pagination(
                page=page, limit=limit, total=total, last_page=max(1, -(-total // limit))
            ),
        )

    async def get_inspection_kpis(self) -> InspectionKpis:
        rows = await self._all(select(ResultRow).where(ResultRow.tenant_id == self.tenant_id))
        total = len(rows)
        passed = sum(1 for r in rows if r.verdict == Verdict.PASS)
        review = sum(1 for r in rows if r.verdict == Verdict.REVIEW)
        failed = sum(1 for r in rows if r.verdict == Verdict.FAIL)
        return InspectionKpis(
            first_pass_yield=round(passed / total, 4) if total else 0.0,
            review_share=round(review / total, 4) if total else 0.0,
            failure_count=failed,
            period="shift-ini",
        )

    async def get_visual(self, vin: str) -> VisualInspection:
        car = await self._car_by_vin(vin)
        result = await self._latest_result(car.id, "visual") if car else None
        if result is None:
            raise ApiError(404, "inspection_not_found", f"No visual inspection for {vin}")
        findings = await self._all(
            select(FindingRow).where(FindingRow.inspection_result_id == result.id)
        )
        mismatches = await self._all(
            select(MismatchRow).where(MismatchRow.inspection_result_id == result.id)
        )
        return VisualInspection(
            result_id=result.result_id,
            vin=vin,
            verdict=result.verdict,
            at=result.created_at,
            review_status=_review_status(result.review_status),
            reviewed_by=result.reviewed_by,
            review_decision=result.review_decision,
            proposal_id=result.proposal_id,
            findings=[_finding_dto(f) for f in findings],
            mismatches=[_mismatch_dto(m) for m in mismatches],
        )

    async def get_functional(self, vin: str) -> FunctionalInspection:
        car = await self._car_by_vin(vin)
        result = await self._latest_result(car.id, "functional") if car else None
        if result is None:
            raise ApiError(404, "inspection_not_found", f"No functional inspection for {vin}")
        steps = await self._all(
            select(StepRow)
            .where(StepRow.inspection_result_id == result.id)
            .order_by(StepRow.step_number)
        )
        return FunctionalInspection(
            result_id=result.result_id,
            vin=vin,
            verdict=result.verdict,
            at=result.created_at,
            review_status=_review_status(result.review_status),
            reviewed_by=result.reviewed_by,
            review_decision=result.review_decision,
            proposal_id=result.proposal_id,
            steps=[_step_dto(s) for s in steps],
        )

    async def _build_report(self, report: ReportRow, car: Car) -> Report:
        tickets = await self._all(select(TicketRow).where(TicketRow.car_id == car.id))
        visual = await self._latest_result(car.id, "visual")
        functional = await self._latest_result(car.id, "functional")

        findings: list[VisualFinding] = []
        if visual is not None:
            rows = await self._all(
                select(FindingRow).where(FindingRow.inspection_result_id == visual.id)
            )
            findings = [_finding_dto(f) for f in rows]
        failures: list[FunctionalStep] = []
        if functional is not None:
            rows = await self._all(
                select(StepRow).where(StepRow.inspection_result_id == functional.id)
            )
            failures = [_step_dto(s) for s in rows if _is_failed_step(s)]

        return Report(
            vin=car.vin,
            verdict=report.verdict,
            confirmed_by=report.confirmed_by,
            confirmed_at=report.confirmed_at,
            summary=report.summary,
            open_findings=len(findings) + len(failures),
            visual_findings=findings,
            functional_failures=failures,
            rework_tickets=[_ticket_dto(t, car.vin) for t in tickets],
            reinspection_status=_reinspection_status(report.reinspection_status),
            updated_at=report.updated_at,
        )

    async def list_reports(
        self,
        verdict: str | None = None,
        vin: str | None = None,
        bay: str | None = None,
        status: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> Page[Report]:
        # shortcut: reports are few; filter/paginate in-process because bay/status span tickets
        rows = await self._all(
            select(ReportRow)
            .where(ReportRow.tenant_id == self.tenant_id)
            .order_by(ReportRow.updated_at.desc())
        )
        cars = await self._cars_by_ids({r.car_id for r in rows})
        built: list[Report] = []
        for report in rows:
            car = cars.get(report.car_id)
            if car is None:
                continue
            if verdict and report.verdict.value != verdict:
                continue
            if vin and vin.lower() not in car.vin.lower():
                continue
            dto = await self._build_report(report, car)
            if bay and not any(t.bay == bay for t in dto.rework_tickets):
                continue
            if status and not any(t.status.value == status.upper() for t in dto.rework_tickets):
                continue
            built.append(dto)
        total = len(built)
        start = (page - 1) * limit
        return Page(
            items=built[start : start + limit],
            pagination=Pagination(
                page=page, limit=limit, total=total, last_page=max(1, -(-total // limit))
            ),
        )

    async def get_report(self, vin: str) -> Report:
        car = await self._car_by_vin(vin)
        report = (
            await self._first(
                select(ReportRow).where(
                    ReportRow.tenant_id == self.tenant_id, ReportRow.car_id == car.id
                )
            )
            if car
            else None
        )
        if report is None:
            raise ApiError(404, "report_not_found", f"No report for {vin}")
        return await self._build_report(report, car)

    async def get_rework_bays(self) -> ReworkBayList:
        bays = await self._all(select(BayRow).where(BayRow.tenant_id == self.tenant_id))
        return ReworkBayList(items=[rework_bay_dto(b, self.plant_state) for b in bays])

    # ---------------------------------------------------------------- mutations

    async def complete_rework(self, vin: str, actor_id: str) -> dict:
        car = await self._car_by_vin(vin)
        if car is None:
            raise ApiError(404, "car_not_found", f"Vehicle {vin} not found")
        ticket = await self._first(
            select(TicketRow)
            .where(TicketRow.car_id == car.id, TicketRow.status != "done")
            .order_by(TicketRow.created_at.desc())
        )
        if ticket is None:
            raise ApiError(404, "ticket_not_found", f"No active rework ticket for {vin}")

        ticket.status = "done"
        ticket.done_by = actor_id
        ticket.done_at = utc_now()
        self.session.add(ticket)

        report = await self._first(select(ReportRow).where(ReportRow.car_id == car.id))
        if report is not None:
            report.reinspection_status = "waiting"
            self.session.add(report)

        car.state = CarState.WAITING_DISPATCH
        car.flags = [f for f in (car.flags or []) if f != "rework"]
        self.session.add(car)
        if self.plant_state is not None:
            self.plant_state.release(vin)
        await self.session.commit()

        await self._dispatch(
            MissionGo(mission_id=generate_ulid(), vin=vin, waypoints=["inspection_gate"])
        )
        if self.event_bus is not None:
            await self.event_bus.publish(
                Topic.VEHICLE_STATUS,
                {"type": "rework.completed", "vin": vin, "reinspect_failed_only": True},
            )
        return {
            "ok": True,
            "vin": vin,
            "ticket_id": ticket.ticket_id,
            "status": "DONE",
            "reinspection_status": "WAITING",
            "done_by": actor_id,
        }

    async def _dispatch(self, command) -> None:
        if self.mission_client is None:
            return
        try:
            await self.mission_client.send(command)
        except Exception:
            log.exception("rework reinspection dispatch failed: %s", command)


def _ticket_dto(t: TicketRow, vin: str) -> ReworkTicket:
    return ReworkTicket(
        ticket_id=t.ticket_id,
        vin=vin,
        kind=t.defect_type,
        bay=t.bay_code,
        status=_ticket_status(t.status),
        created_at=t.created_at,
        updated_at=t.updated_at,
        done_by=t.done_by,
    )
