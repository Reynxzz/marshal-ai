"""Live mode contract: Firestore records answer the same endpoints as the seeded demo."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from marshal_ai.events import InMemoryBus, Topic
from marshal_ai.plant.state import PlantState
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
    Verdict as LiveVerdict,
    ZoneFinding as LiveFinding,
)
from marshal_ai.store import InMemoryStore

from marshal_api.config import Settings, get_settings
from marshal_api.core.deps import get_store
from marshal_api.main import app
from marshal_api.bridge import get_event_bus, get_mission_client, get_plant_state
from marshal_api.core.deps import get_session
from marshal_api.streaming.sse import _live

PREFIX = "/api/v1"
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def _seed(store: InMemoryStore) -> None:
    store.put(
        LiveCar(
            vin="VIN-1",
            build_sheet={"model": "sedan", "colour": "blue", "truck_id": "T-1"},
            state=LiveCarState.YARD,
            location="yard.Y1",
            yard_slot="yard.Y1",
            updated_at=NOW,
        )
    )
    store.put(
        LiveCar(
            vin="VIN-2",
            build_sheet={"model": "sedan", "colour": "black"},
            state=LiveCarState.INSPECTION_GATE,
            location="inspection_gate",
            updated_at=NOW,
        )
    )
    store.put(
        LiveCar(
            vin="VIN-3",
            build_sheet={"model": "sedan", "colour": "red", "truck_id": "T-1"},
            state=LiveCarState.LINE_END,
            location="line_end",
            mission_id="M-1",
            updated_at=NOW,
        )
    )
    store.put(
        LiveCar(
            vin="VIN-4",
            build_sheet={"model": "sedan", "colour": "white"},
            state=LiveCarState.REPORT,
            location="report_point",
            updated_at=NOW,
        )
    )


def _seed_rest(store: InMemoryStore) -> None:
    store.put(
        LiveCar(
            vin="VIN-5",
            build_sheet={"model": "sedan", "colour": "green", "truck_id": "T-1"},
            state=LiveCarState.YARD,
            location="yard.Y2",
            flags=["held"],
            updated_at=NOW,
        )
    )
    store.put(
        LiveCase(
            case_id="AC-1",
            vin="VIN-4",
            location="report_point",
            situation="box in lane, a person is nearby",
            options=["wait_for_removal", "pass_left_3kmh"],
            ranking=[
                {"option": "wait_for_removal", "reason": "person in view", "risk": "low"},
                {"option": "pass_left_3kmh", "reason": "could pass slowly", "risk": "high"},
            ],
            objects=[
                {
                    "label": "box",
                    "kind": "box",
                    "box_2d": [100, 200, 300, 400],
                    "image": "gs://bucket/box.jpg",
                }
            ],
            people_or_moving_equipment=True,
            recommended="wait_for_removal",
            confidence=0.9,
            clip_uri="gs://bucket/assist/AC-1.mp4",
            opened_at=NOW,
        )
    )
    store.put(
        LiveProposal(
            proposal_id="P-1",
            kind="review",
            summary="Visual review for VIN-2",
            payload={"vin": "VIN-2"},
            status=LiveProposalStatus.PENDING,
            created_at=NOW,
        )
    )
    store.put(
        LiveProposal(
            proposal_id="P-2",
            kind="replan",
            summary="Truck T-1 is 40 minutes late; re-slot its cars",
            payload={"truck_id": "T-1"},
            status=LiveProposalStatus.PENDING,
            created_at=NOW,
        )
    )
    store.put(
        LiveInspection(
            vin="VIN-2",
            kind="visual",
            verdict=LiveVerdict.FAIL,
            findings=[
                LiveFinding(
                    zone="left_door",
                    type="scratch",
                    size_cm=4.0,
                    confidence=0.95,
                    image="gs://bucket/gate/VIN-2/left.jpg",
                    observation="a scratch along the left door",
                )
            ],
            build_sheet_mismatches=["colour: build sheet says blue, car shows red (1.00)"],
            mismatch_findings=[
                LiveFinding(
                    zone="whole_car",
                    type="wrong_colour",
                    confidence=1.0,
                    image="gs://bucket/gate/VIN-2/left.jpg",
                    observation="colour: build sheet says blue, car shows red (1.00)",
                )
            ],
            created_at=NOW,
        )
    )
    store.put(
        LiveInspection(
            vin="VIN-2",
            kind="functional",
            verdict=LiveVerdict.FAIL,
            checklist=[
                LiveStep(
                    step=3,
                    name="brake_lights",
                    telemetry_ok=False,
                    camera_ok=True,
                    camera_confidence=0.99,
                    observation="one brake light is dark in the clip",
                    clip_uri="gs://bucket/track/VIN-2/step3.mp4",
                )
            ],
            created_at=NOW,
        )
    )
    store.put(
        LiveReport(
            vin="VIN-2",
            verdict=LiveVerdict.FAIL,
            summary="One scratch and one dark brake light.",
            findings=[
                LiveFinding(
                    zone="left_door",
                    type="scratch",
                    confidence=0.95,
                    image="gs://bucket/gate/VIN-2/left.jpg",
                    observation="a scratch along the left door",
                )
            ],
            failed_checks=[
                LiveStep(
                    step=3,
                    name="brake_lights",
                    telemetry_ok=False,
                    camera_ok=True,
                    clip_uri="gs://bucket/track/VIN-2/step3.mp4",
                )
            ],
            rework=[
                LiveTicket(
                    bay="rework.electrical",
                    defect_type="brake_light",
                    reason="left brake light dark",
                    confirmed_by="sup-1",
                )
            ],
            updated_at=NOW,
        )
    )
    store.put(
        LiveScenario(
            id="SC-0142",
            spec={"description": "A worker steps out at dusk"},
            source="assistance case AC-1",
            status=LiveScenarioStatus.IN_LIBRARY,
            owner="engineer-kim",
            tags=["regression", "from-incident"],
            notes="validated\nwarning: trigger distance is at the edge",
            created_at=NOW,
        )
    )
    store.put(
        LiveRun(
            run_id="R-1",
            scenario_id="SC-0142",
            variant="SC-0142-v17",
            params={"light": "night", "trigger_distance_m": 12.0},
            metrics={"min_gap_to_person_m": 1.2},
            passed=False,
            failed_criteria=["min_gap_to_person_m"],
            clip_uri="gs://bucket/tests/SC-0142-v17.mp4",
            created_at=NOW,
        )
    )
    store.put(
        LiveRelease(
            release="behavior-agent-1.0",
            total=2,
            passed=["SC-0142"],
            failed={"SC-0158": ["no_contact"]},
            evaluated_at=NOW,
        )
    )


@pytest.fixture
def live_client(monkeypatch):
    store = InMemoryStore()
    _seed(store)
    _seed_rest(store)

    async def _session():
        yield None

    app.dependency_overrides[get_settings] = lambda: Settings(store="gcp", bus="memory")
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_event_bus] = lambda: InMemoryBus()
    app.dependency_overrides[get_mission_client] = lambda: None
    app.dependency_overrides[get_plant_state] = lambda: PlantState()
    app.dependency_overrides[get_session] = _session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_overview_counts_live_records(live_client) -> None:
    body = live_client.get(f"{PREFIX}/overview").json()
    assert body["cars"]["total"] == 5
    assert body["cars"]["by_state"]["driving"] == 1
    assert body["cars"]["by_state"]["exception"] == 1
    assert body["cars"]["by_state"]["held"] == 2
    assert body["cars"]["exception"] == 1
    assert body["cars"]["held"] == 2
    assert body["assistance_open"] == 1
    assert body["proposals_pending"] == 2
    assert body["lab"] == {"scenarios_pending_review": 0, "releases_blocked": 1}
    assert len(body["attention"]["assistance"]) == 1
    assert body["attention"]["assistance"][0]["detected_objects"][0]["label"] == "box"
    assert body["attention"]["assistance"][0]["media_ids"][0].startswith("live:")
    assert {p["kind"] for p in body["attention"]["proposals"]} == {
        "replan",
        "inspection_review",
    }


def test_kpis_live(live_client) -> None:
    items = {k["key"]: k for k in live_client.get(f"{PREFIX}/kpis").json()["items"]}
    assert items["active"]["value"] == 1
    assert items["assistance_open"]["value"] == 1
    assert items["proposals_pending"]["value"] == 2
    assert items["exception"]["value"] == 3  # one exception, two held
    assert items["first_pass"]["value"] == 0
    assert items["first_pass"]["tone"] == "danger"
    assert items["releases_blocked"]["value"] == 1


def test_yard_live(live_client) -> None:
    body = live_client.get(f"{PREFIX}/yard").json()
    states = {c["vin"]: c["state"] for c in body["cars"]}
    assert states["VIN-1"] == "parked"
    assert states["VIN-2"] == "held"  # a pending review proposal waits on a human
    assert states["VIN-3"] == "driving"
    assert states["VIN-4"] == "exception"
    assert states["VIN-5"] == "held"
    assert len(body["trucks"]) == 1
    assert body["trucks"][0]["truck_id"] == "T-1"
    assert body["trucks"][0]["assigned_vins"] == ["VIN-1", "VIN-3", "VIN-5"]
    bays = {b["bay_id"]: b for b in body["bays"]}
    assert bays["rework.electrical"]["occupied"] == 1
    assert bays["rework.electrical"]["status"] == "full"


def test_cars_filter_and_detail(live_client) -> None:
    body = live_client.get(f"{PREFIX}/cars", params={"state": "held"}).json()
    assert body["pagination"]["total"] == 2
    assert {c["vin"] for c in body["items"]} == {"VIN-2", "VIN-5"}
    detail = live_client.get(f"{PREFIX}/cars/VIN-1").json()
    assert detail["build_sheet"] == {
        "model": "sedan",
        "trim": "standard",
        "color": "blue",
        "options": [],
    }
    assert live_client.get(f"{PREFIX}/cars/NOPE").status_code == 404


def test_proposals_live(live_client) -> None:
    body = live_client.get(f"{PREFIX}/proposals", params={"kind": "inspection_review"}).json()
    assert [p["proposal_id"] for p in body["items"]] == ["P-1"]
    assert body["items"][0]["vin"] == "VIN-2"
    assert body["items"][0]["allowed_actions"] == ["confirm", "reject"]


def test_assistance_live(live_client) -> None:
    body = live_client.get(f"{PREFIX}/assistance/cases").json()
    assert body["pagination"]["total"] == 1
    case = body["items"][0]
    assert case["status"] == "OPEN"
    assert case["recommendation"]["option_id"] == "wait_for_removal"
    assert case["options"][0]["risk"] == "low"
    assert case["source"] == "car_evidence"
    assert case["clip_uri"] == "gs://bucket/assist/AC-1.mp4"
    assert live_client.get(f"{PREFIX}/assistance/cases/AC-1").json()["vin"] == "VIN-4"
    assert live_client.get(f"{PREFIX}/assistance/cases/NOPE").status_code == 404


def test_quality_live(live_client) -> None:
    results = live_client.get(f"{PREFIX}/inspection/results").json()
    assert results["pagination"]["total"] == 2
    visual = live_client.get(f"{PREFIX}/cars/VIN-2/inspection/visual").json()
    assert visual["verdict"] == "FAIL"
    assert visual["findings"][0]["media_id"].startswith("live:")
    assert visual["mismatches"][0]["attribute"] == "colour"
    assert visual["mismatches"][0]["expected"] == "blue"
    assert visual["mismatches"][0]["found"] == "red"
    functional = live_client.get(f"{PREFIX}/cars/VIN-2/inspection/functional").json()
    assert functional["steps"][0]["disagree"] is True
    report = live_client.get(f"{PREFIX}/cars/VIN-2/report").json()
    assert report["rework_tickets"][0]["bay"] == "rework.electrical"
    assert report["rework_tickets"][0]["status"] == "IN_REPAIR"
    assert report["open_findings"] == 2
    assert live_client.get(f"{PREFIX}/reports").json()["pagination"]["total"] == 1
    bays = live_client.get(f"{PREFIX}/rework/bays").json()
    assert {b["bay_id"] for b in bays["items"]} >= {"rework.electrical"}
    assert live_client.get(f"{PREFIX}/cars/NOPE/inspection/visual").status_code == 404


def test_lab_live(live_client) -> None:
    scenarios = live_client.get(f"{PREFIX}/lab/scenarios").json()
    assert scenarios["pagination"]["total"] == 1
    assert scenarios["items"][0]["status"] == "runnable"
    detail = live_client.get(f"{PREFIX}/lab/scenarios/SC-0142").json()
    assert detail["summary"] == "A worker steps out at dusk"
    assert detail["adversarial_result"]["verdict"] == "FRAGILE"
    assert detail["adversarial_result"]["variants"][0]["clip_id"].startswith("live:")
    releases = live_client.get(f"{PREFIX}/lab/releases").json()
    assert releases["items"][0]["gate"] == "blocked"
    assert releases["items"][0]["failed"] == 1
    assert live_client.get(f"{PREFIX}/lab/test-requests").json()["pagination"]["total"] == 0
    assert live_client.get(f"{PREFIX}/lab/scenarios/NOPE").status_code == 404


def test_live_writes_are_unavailable(live_client) -> None:
    assert (
        live_client.post(
            f"{PREFIX}/proposals/P-1/decide",
            json={"decision": "confirm", "actor_id": "sup-1"},
            headers={"X-Actor-Role": "supervisor"},
        ).status_code
        == 501
    )
    assert (
        live_client.post(
            f"{PREFIX}/assistance/cases/AC-1/decide",
            json={"option": "wait_for_removal", "actor_id": "sup-1"},
            headers={"X-Actor-Role": "supervisor"},
        ).status_code
        == 501
    )
    assert (
        live_client.post(
            f"{PREFIX}/cars/VIN-1/hold",
            json={"reason": "check"},
            headers={"X-Actor-Role": "supervisor"},
        ).status_code
        == 501
    )
    assert (
        live_client.post(
            f"{PREFIX}/cars/VIN-2/rework/done",
            json={"actor_id": "worker-1"},
            headers={"X-Actor-Role": "worker"},
        ).status_code
        == 501
    )
    assert (
        live_client.post(
            f"{PREFIX}/lab/scenarios",
            json={"situation": "a worker steps out", "source": "manual"},
            headers={"X-Actor-Role": "engineer"},
        ).status_code
        == 501
    )


def test_live_media_resolves_gs_uri(live_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "marshal_api.services.media_service._signed_gcs_url",
        lambda uri, ttl_minutes=60: None,
    )
    monkeypatch.setattr(
        "marshal_api.core.gcs.download_bytes", lambda uri, client=None: b"\xff\xd8\xff"
    )
    case = live_client.get(f"{PREFIX}/assistance/cases/AC-1").json()
    media_id = case["media_ids"][0]
    assert media_id.startswith("live:")
    body = live_client.get(f"{PREFIX}/media/{media_id}").json()
    assert body["kind"] == "clip"
    assert body["media_id"] == media_id
    streamed = live_client.get(f"{PREFIX}/media/{media_id}/file")
    assert streamed.status_code == 200
    assert streamed.content.startswith(b"\xff\xd8\xff")
    assert live_client.get(f"{PREFIX}/media/not-a-live-id").status_code == 404


def test_readyz_checks_firestore(live_client) -> None:
    body = live_client.get(f"{PREFIX}/readyz").json()
    assert body["dependencies"][0]["name"] == "firestore"
    assert body["dependencies"][0]["status"] == "ok"


def test_flow_event_from_live_topic() -> None:
    from marshal_ai.driving import MissionDone

    msg = MissionDone(mission_id="M-1", vin="VIN-1", location="inspection_gate")
    event = _live(msg.model_dump(mode="json"), 7, Topic.VEHICLE_STATUS)
    assert event.seq == 7
    assert event.flow == 1
    assert "VIN-1" in event.title or "inspection_gate" in event.title
