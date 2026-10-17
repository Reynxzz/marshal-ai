"""Read-only services backed by the live CARLA records (Firestore).

Live mode answers every GET from the same DTOs as the seeded demo. Mutating
endpoints raise 501: the API has no command channel into the running plant.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import quote

from marshal_ai.plant import load_plant_map
from marshal_ai.records import (
    AssistanceCase as LiveCase,
    Car as LiveCar,
    CarReport as LiveReport,
    InspectionResult as LiveInspection,
    Proposal as LiveProposal,
    ReleaseReport as LiveRelease,
    Scenario as LiveScenario,
    TestRun as LiveRun,
)
from marshal_ai.store import Store

from marshal_api.config import Settings
from marshal_api.core import gcs
from marshal_api.core.errors import ApiError
from marshal_api.live import mapping
from marshal_api.schemas import (
    AssistanceCase,
    AssistanceKpis,
    CarDetail,
    FunctionalInspection,
    InspectionKpis,
    JobAccepted,
    KpiResponse,
    Media,
    Page,
    Pagination,
    Release,
    Report,
    ReworkBayList,
    Scenario,
    ScenarioDetail,
    TestRequest,
    VisualInspection,
    Yard,
    YardMap,
)
from marshal_api.schemas.common import Kpi, KpiTone
from marshal_api.schemas.jobs import MediaKind
from marshal_api.schemas.overview import Overview, OverviewAttention, OverviewCars, OverviewLab
from marshal_api.schemas.yard import YardSummary

ATTENTION_LIMIT = 5

PLANT_NAME = "Plant 01"


def _unavailable(action: str) -> ApiError:
    return ApiError(
        501,
        "live_action_unavailable",
        f"{action} is not available in live mode: the API cannot command the CARLA plant",
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _page(items: list, page: int, limit: int) -> Page:
    total = len(items)
    start = (page - 1) * limit
    return Page(
        items=items[start : start + limit],
        pagination=Pagination(
            page=page, limit=limit, total=total, last_page=max(1, -(-total // limit))
        ),
    )


async def _list(store: Store, model) -> list:
    return await asyncio.to_thread(store.list, model)


class LiveYardService:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def _context(self) -> tuple[set[str], set[str]]:
        cases, proposals = await asyncio.to_thread(
            lambda: (self.store.list(LiveCase), self.store.list(LiveProposal))
        )
        return mapping.open_case_vins(cases), mapping.pending_proposal_vins(proposals)

    async def get_yard_snapshot(self) -> Yard:
        cars, reports = await asyncio.to_thread(
            lambda: (self.store.list(LiveCar), self.store.list(LiveReport))
        )
        open_vins, pending_vins = await self._context()
        states = {c.vin: mapping.dashboard_state(c, open_vins, pending_vins) for c in cars}
        return Yard(
            plant=PLANT_NAME,
            updated_at=_now(),
            summary=YardSummary(
                total=len(cars),
                exception=sum(1 for s in states.values() if s.value == "exception"),
                held=sum(1 for s in states.values() if s.value == "held"),
                zones_closed=0,
            ),
            cars=[mapping.car_summary(c, states[c.vin]) for c in cars],
            trucks=mapping.truck_rows(cars),
            zones=mapping.zone_rows(load_plant_map()),
            bays=mapping.bay_rows(reports),
        )

    async def list_cars(
        self,
        q: str | None = None,
        state: str | None = None,
        location: str | None = None,
        truck: str | None = None,
        flags: list[str] | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> Page:
        cars = await _list(self.store, LiveCar)
        open_vins, pending_vins = await self._context()
        rows = [mapping.car_summary(c, mapping.dashboard_state(c, open_vins, pending_vins)) for c in cars]
        if q:
            needle = q.lower()
            rows = [r for r in rows if needle in r.vin.lower()]
        if state:
            rows = [r for r in rows if r.state.value == state]
        if location:
            rows = [r for r in rows if r.location == location]
        if truck:
            rows = [r for r in rows if r.truck_id == truck]
        if flags:
            wanted = set(flags)
            rows = [r for r in rows if wanted & set(r.flags)]
        # shortcut: live records are few; filtering and pagination run in-process
        return _page(sorted(rows, key=lambda r: r.vin), page, limit)

    async def get_car(self, vin: str) -> CarDetail:
        car = await asyncio.to_thread(self.store.get, LiveCar, vin)
        if car is None:
            raise ApiError(404, "not_found", f"Vehicle {vin} not found")
        open_vins, pending_vins = await self._context()
        return mapping.car_detail(car, mapping.dashboard_state(car, open_vins, pending_vins))

    async def get_map(self) -> YardMap:
        return mapping.yard_map(load_plant_map())

    async def update_truck_eta(self, truck_id: str, eta: str, reason: str | None = None) -> dict:
        raise _unavailable("Changing a truck ETA")

    async def load_truck(self, truck_id: str) -> dict:
        raise _unavailable("Loading a truck")

    async def set_zone_closure(self, zone: str, closed: bool, reason: str | None = None) -> dict:
        raise _unavailable("Closing a zone")

    async def send_mission(self, vin: str, destination: str, reason: str | None = None) -> dict:
        raise _unavailable("Sending a mission")

    async def hold_car(self, vin: str, reason: str, duration_minutes: int | None = None) -> dict:
        raise _unavailable("Holding a car")

    async def resume_car(self, vin: str) -> dict:
        raise _unavailable("Resuming a car")


class LiveAssistanceService:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def list_cases(
        self,
        status: str | None = None,
        open_only: bool = False,
        page: int = 1,
        limit: int = 20,
    ) -> Page:
        cases = [mapping.assistance_case(c) for c in await _list(self.store, LiveCase)]
        if status:
            cases = [c for c in cases if c.status.value == status.upper()]
        if open_only:
            cases = [c for c in cases if c.status.value != "RESOLVED"]
        cases.sort(
            key=lambda c: (
                c.status.value == "RESOLVED",
                c.status.value != "ESCALATED",
                c.opened_at,
            )
        )
        return _page(cases, page, limit)

    async def get_case(self, case_id: str) -> AssistanceCase:
        case = await asyncio.to_thread(self.store.get, LiveCase, case_id)
        if case is None:
            raise ApiError(404, "case_not_found", f"Case {case_id} not found")
        return mapping.assistance_case(case)

    async def get_kpis(self) -> AssistanceKpis:
        cases = await _list(self.store, LiveCase)
        resolved = [c for c in cases if c.resolved_by or c.resolved_at]
        latencies = [
            (c.resolved_at - c.opened_at).total_seconds() / 60
            for c in resolved
            if c.resolved_at
        ]
        average = sum(latencies) / len(latencies) if latencies else 0.0
        return AssistanceKpis(
            open=sum(1 for c in cases if not (c.resolved_by or c.resolved_at)),
            resolved=len(resolved),
            avg_latency_minutes=round(average, 1),
            period="live",
        )

    async def decide_case(self, case_id: str, option_id: str, actor_id: str) -> dict:
        raise _unavailable("Deciding an assistance case")


class LiveProposalService:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def list_proposals(
        self, kind: str | None = None, status: str | None = None, page: int = 1, limit: int = 20
    ) -> Page:
        rows = [mapping.proposal(p) for p in await _list(self.store, LiveProposal)]
        if kind:
            wanted = "inspection_review" if kind == "review" else kind
            rows = [p for p in rows if p.kind.value == wanted]
        if status:
            rows = [p for p in rows if p.status.value == status]
        rows.sort(key=lambda p: p.created_at, reverse=True)
        return _page(rows, page, limit)

    async def decide_proposal(
        self, proposal_id: str, decision: str, actor_id: str, note: str | None = None
    ) -> dict:
        raise _unavailable("Deciding a proposal")


class LiveQualityService:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def _data(self) -> tuple[list[LiveInspection], list[LiveProposal]]:
        return await asyncio.to_thread(
            lambda: (self.store.list(LiveInspection), self.store.list(LiveProposal))
        )

    async def list_results(
        self,
        kind: str | None = None,
        verdict: str | None = None,
        review_status: str | None = None,
        vin: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> Page:
        results, proposals = await self._data()
        rows = [mapping.inspection_result(r, proposals) for r in results]
        if kind:
            rows = [r for r in rows if r.kind.value == kind]
        if verdict:
            rows = [r for r in rows if r.verdict.value == verdict.upper()]
        if review_status:
            rows = [r for r in rows if r.review_status.value == review_status.upper()]
        if vin:
            rows = [r for r in rows if vin.lower() in r.vin.lower()]
        rows.sort(key=lambda r: r.at, reverse=True)
        return _page(rows, page, limit)

    async def get_inspection_kpis(self) -> InspectionKpis:
        results = await _list(self.store, LiveInspection)
        total = len(results)
        passed = sum(1 for r in results if r.verdict.value == "PASS")
        review = sum(1 for r in results if r.verdict.value == "REVIEW")
        failed = sum(1 for r in results if r.verdict.value == "FAIL")
        return InspectionKpis(
            first_pass_yield=round(passed / total, 4) if total else 0.0,
            review_share=round(review / total, 4) if total else 0.0,
            failure_count=failed,
            period="live",
        )

    async def _latest(self, vin: str, kind: str) -> LiveInspection:
        results = [r for r in await _list(self.store, LiveInspection) if r.vin == vin and r.kind == kind]
        if not results:
            raise ApiError(404, "inspection_not_found", f"No {kind} inspection for {vin}")
        return max(results, key=lambda r: r.created_at)

    async def get_visual(self, vin: str) -> VisualInspection:
        result = await self._latest(vin, "visual")
        _, proposals = await self._data()
        return mapping.visual_inspection(result, proposals)

    async def get_functional(self, vin: str) -> FunctionalInspection:
        result = await self._latest(vin, "functional")
        _, proposals = await self._data()
        return mapping.functional_inspection(result, proposals)

    async def list_reports(
        self,
        verdict: str | None = None,
        vin: str | None = None,
        bay: str | None = None,
        status: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> Page:
        rows = [mapping.report(r) for r in await _list(self.store, LiveReport)]
        if verdict:
            rows = [r for r in rows if r.verdict.value == verdict.upper()]
        if vin:
            rows = [r for r in rows if vin.lower() in r.vin.lower()]
        if bay:
            rows = [r for r in rows if any(t.bay == bay for t in r.rework_tickets)]
        if status:
            wanted = status.upper()
            rows = [r for r in rows if any(t.status.value == wanted for t in r.rework_tickets)]
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return _page(rows, page, limit)

    async def get_report(self, vin: str) -> Report:
        row = await asyncio.to_thread(self.store.get, LiveReport, vin)
        if row is None:
            raise ApiError(404, "report_not_found", f"No report for {vin}")
        return mapping.report(row)

    async def get_rework_bays(self) -> ReworkBayList:
        return ReworkBayList(items=mapping.bay_rows(await _list(self.store, LiveReport)))

    async def complete_rework(self, vin: str, actor_id: str) -> dict:
        raise _unavailable("Completing rework")


class LiveLabService:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def list_test_requests(
        self, status: str | None = None, page: int = 1, limit: int = 20
    ) -> Page[TestRequest]:
        # shortcut: live runs do not record test requests; scenarios carry their source only
        return _page([], page, limit)

    async def list_scenarios(
        self, status: str | None = None, tag: str | None = None, page: int = 1, limit: int = 20
    ) -> Page[Scenario]:
        rows = [mapping.scenario(s) for s in await _list(self.store, LiveScenario)]
        if status:
            rows = [s for s in rows if s.status.value == status]
        if tag:
            rows = [s for s in rows if tag in s.tags]
        rows.sort(key=lambda s: s.created_at, reverse=True)
        return _page(rows, page, limit)

    async def get_scenario(self, scenario_id: str) -> ScenarioDetail:
        row = await asyncio.to_thread(self.store.get, LiveScenario, scenario_id)
        if row is None:
            raise ApiError(404, "scenario_not_found", f"Scenario {scenario_id} not found")
        runs = [
            r
            for r in await _list(self.store, LiveRun)
            if r.scenario_id == scenario_id
        ]
        return mapping.scenario_detail(row, runs)

    async def list_releases(self, page: int = 1, limit: int = 20) -> Page[Release]:
        rows = [mapping.release(r) for r in await _list(self.store, LiveRelease)]
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return _page(rows, page, limit)

    async def get_release(self, release_id: str) -> Release:
        row = await asyncio.to_thread(self.store.get, LiveRelease, release_id)
        if row is None:
            raise ApiError(404, "release_not_found", f"Release {release_id} not found")
        return mapping.release(row)

    async def create_scenario(self, situation: str, source: str, request_id: str | None) -> JobAccepted:
        raise _unavailable("Generating a scenario")

    async def review_scenario(
        self, scenario_id: str, decision: str, actor_id: str, note: str | None = None
    ) -> dict:
        raise _unavailable("Reviewing a scenario")

    async def start_adversarial(self, scenario_id: str) -> JobAccepted:
        raise _unavailable("Starting an adversarial search")

    async def evaluate_release(self, release_id: str) -> JobAccepted:
        raise _unavailable("Evaluating a release")

    async def approve_release(self, release_id: str, actor_id: str) -> dict:
        raise _unavailable("Approving a release")


class LiveOverviewService:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def _all(self):
        return await asyncio.to_thread(
            lambda: (
                self.store.list(LiveCar),
                self.store.list(LiveCase),
                self.store.list(LiveProposal),
                self.store.list(LiveScenario),
                self.store.list(LiveRelease),
                self.store.list(LiveInspection),
            )
        )

    async def overview(self) -> Overview:
        cars, cases, proposals, scenarios, releases, _results = await self._all()
        open_vins = mapping.open_case_vins(cases)
        pending_vins = mapping.pending_proposal_vins(proposals)
        summaries = [
            mapping.car_summary(c, mapping.dashboard_state(c, open_vins, pending_vins))
            for c in cars
        ]
        by_state: dict[str, int] = {}
        for car in summaries:
            by_state[car.state.value] = by_state.get(car.state.value, 0) + 1

        open_cases = [
            mapping.assistance_case(c)
            for c in cases
            if not (c.resolved_by or c.resolved_at)
        ]
        open_cases.sort(key=lambda c: c.opened_at, reverse=True)
        pending = [
            mapping.proposal(p) for p in proposals if p.status.value == "pending"
        ]
        pending.sort(key=lambda p: p.created_at, reverse=True)
        return Overview(
            plant=PLANT_NAME,
            updated_at=_now(),
            cars=OverviewCars(
                total=len(summaries),
                by_state=by_state,
                exception=by_state.get("exception", 0),
                held=by_state.get("held", 0),
            ),
            assistance_open=len(open_cases),
            proposals_pending=len(pending),
            lab=OverviewLab(
                scenarios_pending_review=sum(
                    1 for s in scenarios if s.status.value == "valid"
                ),
                releases_blocked=sum(1 for r in releases if r.failed),
            ),
            attention=OverviewAttention(
                assistance=open_cases[:ATTENTION_LIMIT],
                proposals=pending[:ATTENTION_LIMIT],
            ),
            yard_exceptions=[
                c for c in summaries if c.state.value in ("exception", "held")
            ],
        )

    async def kpis(self) -> KpiResponse:
        cars, cases, proposals, _scenarios, releases, results = await self._all()
        open_vins = mapping.open_case_vins(cases)
        pending_vins = mapping.pending_proposal_vins(proposals)
        states = [
            mapping.dashboard_state(c, open_vins, pending_vins).value for c in cars
        ]
        active = states.count("driving") + states.count("waiting_dispatch")
        exception = states.count("exception") + states.count("held")
        open_cases = sum(1 for c in cases if not (c.resolved_by or c.resolved_at))
        pending = sum(1 for p in proposals if p.status.value == "pending")
        blocked = sum(1 for r in releases if r.failed)
        total = len(results)
        first_pass = (
            round(sum(1 for r in results if r.verdict.value == "PASS") / total * 100)
            if total
            else 0
        )
        return KpiResponse(
            items=[
                Kpi(
                    key="active",
                    label="Mobil Aktif",
                    value=active,
                    unit="unit",
                    tone=KpiTone.INFO,
                ),
                Kpi(
                    key="assistance_open",
                    label="Assistance Open",
                    value=open_cases,
                    unit="case",
                    tone=KpiTone.DANGER if open_cases else KpiTone.SUCCESS,
                ),
                Kpi(
                    key="proposals_pending",
                    label="Proposal Pending",
                    value=pending,
                    unit="item",
                    tone=KpiTone.WARNING if pending else KpiTone.SUCCESS,
                ),
                Kpi(
                    key="exception",
                    label="Exception / Held",
                    value=exception,
                    unit="unit",
                    tone=KpiTone.DANGER if states.count("exception") else KpiTone.DEFAULT,
                ),
                Kpi(
                    key="first_pass",
                    label="First-pass Yield",
                    value=first_pass,
                    unit="%",
                    tone=(
                        KpiTone.DEFAULT
                        if total == 0
                        else KpiTone.SUCCESS
                        if first_pass >= 80
                        else KpiTone.WARNING
                        if first_pass >= 60
                        else KpiTone.DANGER
                    ),
                ),
                Kpi(
                    key="releases_blocked",
                    label="Release Blocked",
                    value=blocked,
                    unit="release",
                    tone=KpiTone.DANGER if blocked else KpiTone.SUCCESS,
                ),
            ]
        )


class LiveAssistIngestService:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def ingest(
        self,
        *,
        clip_uri: str,
        vin: str,
        location: str,
        options: list[str],
        situation: str | None = None,
    ) -> str:
        raise _unavailable("Ingesting an assistance clip")


class LiveMediaService:
    """Live media ids are base64url tokens over the records' ``gs://`` URIs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _uri(self, media_id_value: str) -> str:
        try:
            return mapping.media_uri(media_id_value)
        except ValueError as exc:
            raise ApiError(404, "media_not_found", f"Media {media_id_value} is unknown") from exc

    async def resolve_media(self, media_id_value: str) -> Media:
        uri = self._uri(media_id_value)
        content_type = gcs.guess_mime(uri)
        return Media(
            media_id=media_id_value,
            kind=MediaKind.IMAGE if content_type.startswith("image/") else MediaKind.CLIP,
            content_type=content_type,
            url=self._url(media_id_value, uri),
            available=True,
        )

    async def read_media(self, media_id_value: str) -> tuple[bytes, str]:
        uri = self._uri(media_id_value)
        data = await asyncio.to_thread(gcs.download_bytes, uri)
        return data, gcs.guess_mime(uri)

    def _url(self, media_id_value: str, uri: str) -> str:
        from marshal_api.services.media_service import _signed_gcs_url

        signed = _signed_gcs_url(uri)
        if signed:
            return signed
        return f"{self.settings.api_prefix}/media/{quote(media_id_value, safe='')}/file"
