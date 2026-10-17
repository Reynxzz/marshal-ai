"""Live CARLA records (Firestore) to dashboard DTOs.

Input is always a ``marshal_ai.records`` object; output is always a
``marshal_api.schemas`` DTO, so the HTTP contract is identical in demo and live mode.
Nothing here writes: live mode is read-only.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from marshal_ai.plant import PlantMap
from marshal_ai.records import (
    AssistanceCase as LiveCase,
    Car as LiveCar,
    CarReport as LiveReport,
    CarState as LiveCarState,
    ChecklistItem as LiveStep,
    InspectionResult as LiveInspection,
    Proposal as LiveProposal,
    ProposalStatus as LiveProposalStatus,
    ReleaseReport as LiveRelease,
    ReworkTicket as LiveTicket,
    Scenario as LiveScenario,
    ScenarioStatus as LiveScenarioStatus,
    TestRun as LiveRun,
    ZoneFinding as LiveFinding,
)

from marshal_api.schemas import (
    AssistanceCase,
    AssistanceOption,
    AssistanceSource,
    BuildSheet,
    BuildSheetMismatch,
    CarDetail,
    CarSummary,
    DetectedObject,
    FunctionalInspection,
    FunctionalStep,
    InspectionResult,
    MapPoint,
    Proposal,
    Release,
    ReleaseEvalTest,
    Report,
    ReworkBay,
    ReworkTicket,
    Scenario,
    ScenarioDetail,
    Truck,
    ValidatorNote,
    VisualFinding,
    VisualInspection,
    YardMap,
    Zone,
)
from marshal_api.schemas.assistance import (
    AssistanceRecommendation,
    AssistanceStatus,
    RiskLevel,
)
from marshal_api.schemas.common import CarState, ReviewStatus, Verdict
from marshal_api.schemas.lab import (
    AdversarialResult,
    AdversarialVerdict,
    ReleaseEvaluationStatus,
    ReleaseGate,
    ScenarioStatus,
    VariantRun,
    ValidatorSeverity,
)
from marshal_api.schemas.quality import (
    InspectionKind,
    ReinspectionStatus,
    ReworkTicketStatus,
)
from marshal_api.schemas.yard import MapPointKind, ReworkBayStatus

# The live process states and the dashboard's operational states.
_LIVE_STATE: dict[LiveCarState, CarState] = {
    LiveCarState.LINE_END: CarState.READY,
    LiveCarState.INSPECTION_GATE: CarState.INSPECTING,
    LiveCarState.REVIEW: CarState.INSPECTING,
    LiveCarState.TEST_TRACK: CarState.INSPECTING,
    LiveCarState.REPORT: CarState.INSPECTING,
    LiveCarState.REWORK: CarState.REWORK,
    LiveCarState.YARD: CarState.PARKED,
    LiveCarState.SHIPPED: CarState.READY,
}

# The live rework bays and their dashboard names (marshal_ai.flows.report.BAYS).
LIVE_BAYS: dict[str, str] = {
    "rework.paint": "Paint Touch-up Bay",
    "rework.body": "Body Fit Bay",
    "rework.electrical": "Electrical Bay",
    "rework.mechanical": "Mechanical Bay",
    "production_control": "Production Control Hold",
}

_SCENARIO_STATUS: dict[LiveScenarioStatus, ScenarioStatus] = {
    LiveScenarioStatus.DRAFT: ScenarioStatus.DRAFT,
    LiveScenarioStatus.VALID: ScenarioStatus.PENDING_REVIEW,
    LiveScenarioStatus.DRY_RUN_OK: ScenarioStatus.APPROVED,
    LiveScenarioStatus.IN_LIBRARY: ScenarioStatus.RUNNABLE,
    LiveScenarioStatus.REJECTED: ScenarioStatus.REJECTED,
}

_PROPOSAL_KIND = {"replan": "replan", "review": "inspection_review", "rework": "rework"}
_CATEGORY = {
    "replan": "Re-plan route",
    "inspection_review": "Visual inspection review",
    "rework": "Rework confirmation",
}
_ACTIONS = {
    "replan": ["approve", "reject"],
    "inspection_review": ["confirm", "reject"],
    "rework": ["confirm", "reject"],
}
_EFFECTS = {
    "replan": (
        "Re-plan approved; the missions are dispatched to the cars.",
        "Plan rejected; the cars stay where they are for a new decision.",
    ),
    "inspection_review": (
        "Review confirmed and the car continues to rework.",
        "Findings rejected; the car is cleared to the test track.",
    ),
    "rework": (
        "Rework confirmed and the car is sent to its bays.",
        "Rework rejected; the car waits for a new decision.",
    ),
}


# --------------------------------------------------------------------------- cars

def open_case_vins(cases: list[LiveCase]) -> set[str]:
    return {c.vin for c in cases if not c.resolved_by and not c.resolved_at}


def pending_proposal_vins(proposals: list[LiveProposal]) -> set[str]:
    return {
        p.payload.get("vin")
        for p in proposals
        if p.status == LiveProposalStatus.PENDING and p.payload.get("vin")
    }


def dashboard_state(car: LiveCar, open_vins: set[str], pending_vins: set[str]) -> CarState:
    if car.vin in open_vins:
        return CarState.EXCEPTION
    if car.vin in pending_vins or "held" in car.flags or car.do_not_ship:
        return CarState.HELD
    if car.mission_id:
        return CarState.DRIVING
    return _LIVE_STATE.get(car.state, CarState.EXCEPTION)


def car_summary(car: LiveCar, state: CarState) -> CarSummary:
    sheet = car.build_sheet
    return CarSummary(
        vin=car.vin,
        model=sheet.model,
        color=sheet.colour,
        state=state,
        location=car.location,
        mission=car.mission_id,
        truck_id=sheet.truck_id,
        slot=car.yard_slot,
        flags=list(car.flags),
        updated_at=car.updated_at,
    )


def car_detail(car: LiveCar, state: CarState) -> CarDetail:
    sheet = car.build_sheet
    # shortcut: the live record has no created_at; updated_at is the closest timestamp it has
    return CarDetail(
        **car_summary(car, state).model_dump(),
        build_sheet=BuildSheet(
            model=sheet.model,
            trim=sheet.trim,
            color=sheet.colour,
            options=list(sheet.options),
        ),
        created_at=car.updated_at,
    )


def truck_rows(cars: list[LiveCar]) -> list[Truck]:
    by_truck: dict[str, list[str]] = {}
    for car in cars:
        truck_id = car.build_sheet.truck_id
        if truck_id:
            by_truck.setdefault(truck_id, []).append(car.vin)
    # shortcut: live trucks only carry the VINs the build sheets name; ETAs need a plant snapshot
    return [
        Truck(truck_id=truck_id, assigned_vins=sorted(vins))
        for truck_id, vins in sorted(by_truck.items())
    ]


def zone_rows(plant_map: PlantMap) -> list[Zone]:
    return [
        Zone(zone=name, name=description or name)
        for name, description in sorted(plant_map.zones.items())
    ]


def bay_rows(reports: list[LiveReport]) -> list[ReworkBay]:
    occupants: dict[str, list[str]] = {bay: [] for bay in LIVE_BAYS}
    for report in reports:
        for ticket in report.rework:
            if ticket.done_at is None and ticket.bay in occupants:
                occupants[ticket.bay].append(report.vin)
    rows = []
    for bay, name in LIVE_BAYS.items():
        active = sorted(occupants[bay])
        rows.append(
            ReworkBay(
                bay_id=bay,
                name=name,
                capacity=1,
                occupied=len(active),
                status=ReworkBayStatus.FULL if active else ReworkBayStatus.OPEN,
                active_vins=active,
            )
        )
    return rows


def yard_map(plant_map: PlantMap) -> YardMap:
    points = [
        MapPoint(id=p.name, name=p.name, kind=MapPointKind.POINT, x=p.x, y=p.y)
        for p in plant_map.points.values()
    ]
    return YardMap(points=sorted(points, key=lambda p: p.id))


# --------------------------------------------------------------------- assistance

def _risk(value: Any) -> RiskLevel:
    try:
        return RiskLevel(str(value).lower())
    except ValueError:
        return RiskLevel.LOW


LIVE_MEDIA_PREFIX = "live:"


def media_id(uri: str | None) -> str | None:
    """A URL-path-safe id for a live ``gs://`` URI.

    The frontend puts the id in a path segment (``/media/{id}``), and a raw ``gs://``
    URI's slashes would be split by the router, so the URI goes in base64url.
    """
    if not uri:
        return None
    if not uri.startswith("gs://"):
        return uri
    token = base64.urlsafe_b64encode(uri.encode()).decode().rstrip("=")
    return f"{LIVE_MEDIA_PREFIX}{token}"


def media_uri(media_id_value: str) -> str:
    """The ``gs://`` URI behind :func:`media_id`."""
    if media_id_value.startswith(LIVE_MEDIA_PREFIX):
        token = media_id_value[len(LIVE_MEDIA_PREFIX) :]
        padding = "=" * (-len(token) % 4)
        try:
            uri = base64.urlsafe_b64decode(token + padding).decode()
        except Exception as exc:
            raise ValueError(f"{media_id_value!r} is not a live media id") from exc
        if uri.startswith("gs://"):
            return uri
        raise ValueError(f"{media_id_value!r} is not a live media id")
    if media_id_value.startswith("gs://"):
        return media_id_value
    raise ValueError(f"{media_id_value!r} is not a live media id")


def _media_ids(uris: list[str | None]) -> list[str]:
    seen: list[str] = []
    for uri in uris:
        token = media_id(uri)
        if token and token not in seen:
            seen.append(token)
    return seen


def assistance_case(case: LiveCase) -> AssistanceCase:
    ranking = {r.get("option"): r for r in case.ranking}
    options = sorted(
        (
            AssistanceOption(
                id=option,
                label=option.replace("_", " "),
                risk=_risk((ranking.get(option) or {}).get("risk")),
                reason=(ranking.get(option) or {}).get("reason"),
                rank=index,
            )
            for index, option in enumerate(case.options, start=1)
        ),
        key=lambda o: o.rank or 0,
    )
    if case.resolved_by or case.resolved_at:
        status = AssistanceStatus.RESOLVED
    elif case.escalated:
        status = AssistanceStatus.ESCALATED
    else:
        status = AssistanceStatus.OPEN
    return AssistanceCase(
        case_id=case.case_id,
        vin=case.vin,
        location=case.location,
        situation=case.situation,
        status=status,
        opened_at=case.opened_at,
        escalation="shift lead" if case.escalated else None,
        moving_people_or_equipment=case.people_or_moving_equipment,
        recommendation=(
            AssistanceRecommendation(
                option_id=case.recommended, confidence=case.confidence or 0.0
            )
            if case.recommended
            else None
        ),
        options=options,
        media_ids=_media_ids([case.clip_uri, *(o.get("image") for o in case.objects)]),
        detected_objects=[
            DetectedObject(
                label=str(o.get("label", "object")),
                box=[float(v) for v in (o.get("box_2d") or [])],
                confidence=o.get("confidence"),
            )
            for o in case.objects
        ],
        source=AssistanceSource.CAR_EVIDENCE,
        clip_uri=case.clip_uri,
        resolved_by=case.resolved_by,
        resolved_at=case.resolved_at,
        chosen_option=case.choice,
        result=(
            f"Decision '{case.choice}' dispatched; the vehicle continues."
            if case.choice
            else None
        ),
    )


# ---------------------------------------------------------------------- proposals

def proposal(row: LiveProposal) -> Proposal:
    kind = _PROPOSAL_KIND[row.kind]
    approve, reject = _EFFECTS[kind]
    return Proposal(
        proposal_id=row.proposal_id,
        kind=kind,
        vin=row.payload.get("vin"),
        category=_CATEGORY[kind],
        summary=row.summary,
        payload=dict(row.payload),
        status=row.status.value,
        allowed_actions=list(_ACTIONS[kind]),
        effects={"approve": approve, "reject": reject},
        created_at=row.created_at,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        decision=row.status.value if row.status != LiveProposalStatus.PENDING else None,
    )


# ---------------------------------------------------------------------- quality

def _finding(row: LiveFinding) -> VisualFinding:
    return VisualFinding(
        zone=row.zone,
        defect_type=row.type,
        size=f"{row.size_cm:g} cm" if row.size_cm is not None else None,
        confidence=row.confidence,
        observation=row.observation or "",
        media_id=media_id(row.image),
    )


def _parse_mismatch(text: str) -> tuple[str, str]:
    rest = text.split("build sheet says ", 1)[1] if "build sheet says " in text else text
    expected, _, found = rest.partition(", car shows ")
    return expected.strip(), found.rsplit(" (", 1)[0].strip()


def _mismatch(row: LiveFinding) -> BuildSheetMismatch:
    attribute = row.type.removeprefix("wrong_") or row.zone
    text = row.observation or ""
    if ":" in text and "build sheet says" in text:
        attribute = text.split(":", 1)[0].strip() or attribute
    expected, found = _parse_mismatch(text) if text else ("", "")
    return BuildSheetMismatch(
        attribute=attribute,
        expected=expected,
        found=found,
        confidence=row.confidence,
    )


def _review(proposals: list[LiveProposal], vin: str) -> LiveProposal | None:
    matches = [
        p
        for p in proposals
        if p.kind == "review" and p.payload.get("vin") == vin
    ]
    return max(matches, key=lambda p: p.created_at, default=None)


def inspection_result(result: LiveInspection, proposals: list[LiveProposal]) -> InspectionResult:
    review = _review(proposals, result.vin)
    return InspectionResult(
        result_id=f"{result.vin}:{result.kind}",
        vin=result.vin,
        kind=InspectionKind(result.kind),
        verdict=Verdict(result.verdict.value),
        finding_count=len(result.findings) + len(result.mismatch_findings),
        failed_check_count=sum(1 for item in result.checklist if not item.passed),
        at=result.created_at,
        review_status=(
            ReviewStatus.PENDING
            if review and review.status == LiveProposalStatus.PENDING
            else ReviewStatus.DONE
        ),
        proposal_id=review.proposal_id if review else None,
    )


def visual_inspection(result: LiveInspection, proposals: list[LiveProposal]) -> VisualInspection:
    review = _review(proposals, result.vin)
    mismatches = [_mismatch(m) for m in result.mismatch_findings]
    return VisualInspection(
        result_id=f"{result.vin}:{result.kind}",
        vin=result.vin,
        verdict=Verdict(result.verdict.value),
        at=result.created_at,
        review_status=(
            ReviewStatus.PENDING
            if review and review.status == LiveProposalStatus.PENDING
            else ReviewStatus.DONE
        ),
        reviewed_by=review.decided_by if review else None,
        review_decision=(
            "confirm"
            if review and review.status == LiveProposalStatus.APPROVED
            else "reject"
            if review and review.status == LiveProposalStatus.REJECTED
            else None
        ),
        proposal_id=review.proposal_id if review else None,
        findings=[_finding(f) for f in result.findings],
        mismatches=mismatches,
    )


def _step_side(value: bool | None) -> str | None:
    return None if value is None else ("ok" if value else "fail")


def _step(row: LiveStep) -> FunctionalStep:
    return FunctionalStep(
        step=row.step,
        name=row.name,
        telemetry=_step_side(row.telemetry_ok),
        camera_result=_step_side(row.camera_ok),
        confidence=row.camera_confidence or 0.0,
        observation=row.observation,
        disagree=(
            row.telemetry_ok is not None
            and row.camera_ok is not None
            and row.telemetry_ok != row.camera_ok
        ),
        media_id=media_id(row.clip_uri),
    )


def functional_inspection(
    result: LiveInspection, proposals: list[LiveProposal]
) -> FunctionalInspection:
    review = _review(proposals, result.vin)
    return FunctionalInspection(
        result_id=f"{result.vin}:{result.kind}",
        vin=result.vin,
        verdict=Verdict(result.verdict.value),
        at=result.created_at,
        review_status=(
            ReviewStatus.PENDING
            if review and review.status == LiveProposalStatus.PENDING
            else ReviewStatus.DONE
        ),
        reviewed_by=review.decided_by if review else None,
        proposal_id=review.proposal_id if review else None,
        steps=[_step(i) for i in result.checklist],
    )


def _ticket(row: LiveTicket, vin: str, index: int, at: datetime) -> ReworkTicket:
    return ReworkTicket(
        # shortcut: the live ticket has no id; vin+bay+position is stable for one report version
        ticket_id=f"{vin}:{row.bay}:{index}",
        vin=vin,
        kind=row.defect_type,
        bay=row.bay,
        status=(
            ReworkTicketStatus.DONE
            if row.done_at
            else ReworkTicketStatus.IN_REPAIR
            if row.confirmed_by
            else ReworkTicketStatus.OPEN
        ),
        created_at=row.done_at or at,
        updated_at=row.done_at or at,
        done_by=row.confirmed_by if row.done_at else None,
    )


def _reinspection(row: LiveReport) -> ReinspectionStatus:
    if row.reinspection:
        last = row.reinspection[-1]
        return ReinspectionStatus.PASS if last.verdict.value == "PASS" else ReinspectionStatus.FAIL
    if any(t.done_at for t in row.rework):
        return ReinspectionStatus.WAITING
    return ReinspectionStatus.NOT_REQUIRED


def report(row: LiveReport) -> Report:
    findings = [_finding(f) for f in row.findings]
    failures = [_step(i) for i in row.failed_checks]
    confirmed_by = next((t.confirmed_by for t in row.rework if t.confirmed_by), None)
    return Report(
        vin=row.vin,
        verdict=Verdict(row.verdict.value),
        confirmed_by=confirmed_by,
        confirmed_at=None,
        summary=row.summary,
        open_findings=len(findings) + len(failures),
        visual_findings=findings,
        functional_failures=failures,
        rework_tickets=[
            _ticket(t, row.vin, i, row.updated_at) for i, t in enumerate(row.rework)
        ],
        reinspection_status=_reinspection(row),
        updated_at=row.updated_at,
    )


# -------------------------------------------------------------------------- lab

def _scenario_summary(row: LiveScenario) -> str:
    description = str(row.spec.get("description") or "").strip()
    if description:
        return description
    notes = (row.notes or "").strip().splitlines()
    return notes[0] if notes else row.source


def scenario(row: LiveScenario) -> Scenario:
    return Scenario(
        scenario_id=row.id,
        source=row.source,
        status=_SCENARIO_STATUS.get(row.status, ScenarioStatus.DRAFT),
        tags=list(row.tags),
        owner=row.owner or "system",
        summary=_scenario_summary(row),
        created_at=row.created_at,
    )


def _validator_notes(notes: str) -> list[ValidatorNote]:
    rows = []
    for line in (notes or "").splitlines():
        text = line.strip()
        if not text:
            continue
        lowered = text.lower()
        severity = (
            ValidatorSeverity.ERROR
            if lowered.startswith("error")
            else ValidatorSeverity.WARNING
            if lowered.startswith("warn")
            else ValidatorSeverity.OK
        )
        rows.append(ValidatorNote(severity=severity, message=text))
    return rows


def adversarial(runs: list[LiveRun], failure_report: dict | None = None) -> AdversarialResult | None:
    if not runs:
        return None
    failed = [r for r in runs if not r.passed]
    smallest = min(failed, key=lambda r: len(r.failed_criteria), default=None)
    variants = [
        VariantRun(
            variant_id=r.variant,
            light=str(r.params.get("light", "noon")),
            weather=r.params.get("weather"),
            occluder=r.params.get("occluder"),
            trigger_distance_m=r.params.get("trigger_distance_m"),
            ego_speed_kmh=r.params.get("ego_speed_kmh"),
            min_gap_m=r.metrics.get("min_gap_m"),
            passed=r.passed,
            clip_id=media_id(r.clip_uri),
        )
        for r in runs
    ]
    sample = smallest or runs[0]
    return AdversarialResult(
        runs=len(runs),
        verdict=AdversarialVerdict.FRAGILE if failed else AdversarialVerdict.ROBUST,
        smallest_failing_variant=smallest.variant if smallest else None,
        criteria={"failed": ", ".join(smallest.failed_criteria)} if smallest else None,
        metrics={k: v for k, v in sample.metrics.items()},
        media_ids=_media_ids([r.clip_uri for r in runs]),
        variants=variants,
        failure_report=_failure_report(failure_report),
    )


def _failure_report(raw: dict | None) -> dict | None:
    """The live failure report, keyed as the frontend schema reads it."""
    if not raw:
        return None
    return {
        "variant_id": raw.get("variant"),
        "summary": "; ".join(raw.get("measured") or []) or None,
        "conditions": raw.get("conditions"),
        "likely_cause": raw.get("likely_cause"),
        "suggested_fix": raw.get("suggested_fix"),
        "clip_id": media_id(raw.get("clip_uri")),
    }


def scenario_detail(row: LiveScenario, runs: list[LiveRun]) -> ScenarioDetail:
    detail = scenario(row)
    return ScenarioDetail(
        **detail.model_dump(),
        spec=dict(row.spec),
        validator_notes=_validator_notes(row.notes),
        dry_run_metrics=None,  # shortcut: live scenarios keep the dry-run numbers in the spec only
        review=None,
        adversarial_result=adversarial(runs, row.failure_report),
    )


def release(row: LiveRelease) -> Release:
    tests: list[ReleaseEvalTest] = [
        ReleaseEvalTest(
            test_id=scenario_id,
            scenario_id=scenario_id,
            metric="",
            criteria="",
            result="passed",
            passed=True,
        )
        for scenario_id in row.passed
    ] + [
        ReleaseEvalTest(
            test_id=scenario_id,
            scenario_id=scenario_id,
            metric="",
            criteria=", ".join(criteria),
            result="failed",
            passed=False,
        )
        for scenario_id, criteria in row.failed.items()
    ]
    return Release(
        release_id=row.release,
        evaluation_status=ReleaseEvaluationStatus.DONE,
        gate=(
            ReleaseGate.BLOCKED
            if row.failed
            else ReleaseGate.APPROVED
            if row.approved_by
            else ReleaseGate.PENDING
        ),
        passed=len(row.passed),
        failed=len(row.failed),
        tests=tests,
        approved_by=row.approved_by,
        approved_at=None,
        updated_at=row.evaluated_at,
    )
