"""Phase 2 gate: every scaffolded endpoint is reachable and contract-shaped.

The routes validate their responses against the shared DTOs, so a stub that
drifted from ``marshal-fe/src/lib/schemas.ts`` would surface here as a 500
(ResponseValidationError) rather than a false pass.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from marshal_api.main import app

client = TestClient(app)

PREFIX = "/api/v1"

GET_ENDPOINTS = [
    "/healthz",
    "/readyz",
    "/me",
    "/overview",
    "/kpis",
    "/yard",
    "/cars",
    "/cars/VIN-SCAFFOLD",
    "/map",
    "/proposals",
    "/assistance/cases",
    "/assistance/cases/CASE-0001",
    "/assistance/kpis",
    "/inspection/results",
    "/inspection/kpis",
    "/cars/VIN-SCAFFOLD/inspection/visual",
    "/cars/VIN-SCAFFOLD/inspection/functional",
    "/reports",
    "/cars/VIN-SCAFFOLD/report",
    "/rework/bays",
    "/lab/test-requests",
    "/lab/scenarios",
    "/lab/scenarios/SC-0001",
    "/lab/releases",
    "/lab/releases/REL-0001",
    "/jobs/JOB-0001",
    "/media/MEDIA-0001",
]

# (path, body, X-Actor-Role, expected status)
POST_ENDPOINTS = [
    ("/dev/actor", {"role": "supervisor"}, None, 200),
    ("/trucks/TRK-1/eta", {"eta": "2026-10-10T10:00:00Z"}, "supervisor", 200),
    ("/trucks/TRK-1/load", {}, "supervisor", 200),
    ("/zones/zone-a/closure", {"closed": True}, "supervisor", 200),
    ("/cars/VIN-SCAFFOLD/missions", {"destination": "yard"}, "supervisor", 200),
    ("/cars/VIN-SCAFFOLD/hold", {"reason": "blocked"}, "supervisor", 200),
    ("/cars/VIN-SCAFFOLD/resume", {}, "supervisor", 200),
    ("/proposals/P-1/decide", {"decision": "approve", "actor_id": "sup-1"}, "supervisor", 200),
    (
        "/assistance/cases/CASE-1/decide",
        {"option": "wait", "actor_id": "sup-1"},
        "supervisor",
        200,
    ),
    ("/cars/VIN-SCAFFOLD/rework/done", {"actor_id": "wrk-1"}, "worker", 200),
    ("/lab/scenarios", {"situation": "blocked lane", "source": "incident"}, "engineer", 202),
    (
        "/lab/scenarios/SC-1/review",
        {"decision": "approved", "actor_id": "eng-1"},
        "engineer",
        200,
    ),
    ("/lab/scenarios/SC-1/adversarial-search", {}, "engineer", 202),
    ("/lab/releases/REL-1/evaluate", {}, "engineer", 202),
    ("/lab/releases/REL-1/approve", {"actor_id": "eng-1"}, "engineer", 200),
]


@pytest.mark.parametrize("path", GET_ENDPOINTS)
def test_get_endpoints_are_reachable(path: str) -> None:
    response = client.get(f"{PREFIX}{path}")
    assert response.status_code == 200, (path, response.status_code, response.text)


@pytest.mark.parametrize("path,body,role,expected", POST_ENDPOINTS)
def test_post_endpoints_are_reachable(path, body, role, expected) -> None:
    headers = {"X-Actor-Role": role} if role else {}
    response = client.post(f"{PREFIX}{path}", json=body, headers=headers)
    assert response.status_code == expected, (path, response.status_code, response.text)


def test_page_envelope_matches_frontend_contract() -> None:
    body = client.get(f"{PREFIX}/cars", params={"page": 2, "limit": 5}).json()
    assert body["items"] == []
    assert body["pagination"] == {"page": 2, "limit": 5, "total": 0, "last_page": 1}


def test_yard_snapshot_shape() -> None:
    body = client.get(f"{PREFIX}/yard").json()
    assert set(body) == {"plant", "updated_at", "summary", "cars", "trucks", "zones", "bays"}
    assert set(body["summary"]) == {"total", "exception", "held", "zones_closed"}


def test_overview_shape() -> None:
    body = client.get(f"{PREFIX}/overview").json()
    assert set(body) >= {
        "plant", "updated_at", "cars", "assistance_open",
        "proposals_pending", "lab", "attention", "yard_exceptions",
    }
    assert set(body["attention"]) == {"assistance", "proposals"}


def test_job_and_media_shapes() -> None:
    job = client.get(f"{PREFIX}/jobs/JOB-1").json()
    assert job["job_id"] == "JOB-1"
    assert job["status"] in {"queued", "running", "done", "failed", "timeout", "unknown"}

    media = client.get(f"{PREFIX}/media/MEDIA-1").json()
    assert media["media_id"] == "MEDIA-1"
    assert set(media) >= {"media_id", "kind", "content_type", "url", "available"}


def test_rbac_rejects_wrong_role_with_frontend_error_shape() -> None:
    response = client.post(
        f"{PREFIX}/proposals/P-1/decide",
        json={"decision": "approve", "actor_id": "x"},
        headers={"X-Actor-Role": "worker"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_validation_error_uses_frontend_error_shape() -> None:
    response = client.post(
        f"{PREFIX}/trucks/TRK-1/eta",
        json={},
        headers={"X-Actor-Role": "supervisor"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert "details" in body


def test_actor_identity_and_idempotency_headers() -> None:
    me = client.get(
        f"{PREFIX}/me",
        headers={"X-Actor-ID": "sup-9", "X-Actor-Role": "supervisor", "X-Actor-Name": "Rey"},
    ).json()
    assert me["actor_id"] == "sup-9"
    assert me["role"] == "supervisor"
    assert me["display_name"] == "Rey"

    accepted = client.post(
        f"{PREFIX}/proposals/P-1/decide",
        json={"decision": "approve", "actor_id": "sup-9"},
        headers={"X-Actor-Role": "supervisor", "Idempotency-Key": "decide-P-1"},
    )
    assert accepted.status_code == 200


OPENAPI_PATHS = {
    "/healthz", "/readyz", "/me", "/dev/actor", "/overview", "/kpis",
    "/yard", "/cars", "/cars/{vin}", "/map",
    "/trucks/{truck_id}/eta", "/trucks/{truck_id}/load",
    "/zones/{zone}/closure", "/cars/{vin}/missions",
    "/cars/{vin}/hold", "/cars/{vin}/resume",
    "/proposals", "/proposals/{proposal_id}/decide",
    "/assistance/cases", "/assistance/cases/{case_id}",
    "/assistance/cases/{case_id}/decide", "/assistance/kpis",
    "/inspection/results", "/inspection/kpis",
    "/cars/{vin}/inspection/visual", "/cars/{vin}/inspection/functional",
    "/reports", "/cars/{vin}/report", "/rework/bays", "/cars/{vin}/rework/done",
    "/lab/test-requests", "/lab/scenarios", "/lab/scenarios/{scenario_id}",
    "/lab/scenarios/{scenario_id}/review",
    "/lab/scenarios/{scenario_id}/adversarial-search",
    "/lab/releases", "/lab/releases/{release_id}",
    "/lab/releases/{release_id}/evaluate", "/lab/releases/{release_id}/approve",
    "/jobs/{job_id}", "/media/{media_id}",
}


def test_openapi_and_swagger_available() -> None:
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    documented = set(spec["paths"])
    missing = {f"{PREFIX}{path}" for path in OPENAPI_PATHS} - documented
    assert not missing, missing
