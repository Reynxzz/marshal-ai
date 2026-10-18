"""Tests for sim_bridge endpoints connecting Three.js to marshal_api."""

import pytest
from fastapi.testclient import TestClient

from marshal_api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_bridge_gate_pass(client):
    payload = {
        "vin": "VIN-SIM-01",
        "expectedColour": "red",
        "frames": {"front": "base64_data"},
        "hints": {"front": "colour=red; damage=none"},
    }
    res = client.post("/api/v1/bridge/gate", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["vin"] == "VIN-SIM-01"
    assert data["pass"] is True
    assert data["colourMatch"] is True
    assert data["defects"] == []


def test_bridge_gate_fail_colour(client):
    payload = {
        "vin": "VIN-SIM-02",
        "expectedColour": "blue",
        "frames": {"front": "base64_data"},
        "hints": {"front": "colour=yellow; damage=none"},
    }
    res = client.post("/api/v1/bridge/gate", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["pass"] is False
    assert data["colourMatch"] is False
    assert any(d["kind"] == "wrong_colour" for d in data["defects"])


def test_bridge_lamp_check(client):
    payload = {
        "vin": "VIN-SIM-01",
        "step": "headlights",
        "telemetry": {"headlights": True},
        "frames": ["base64_data"],
        "hint": "headlights=on",
    }
    res = client.post("/api/v1/bridge/lamp", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["pass"] is True
    assert len(data["evidence"]) == 1
    assert data["evidence"][0]["camera"] is True


def test_bridge_assist_incident(client):
    payload = {
        "case_id": "CASE-101",
        "vin": "VIN-SIM-03",
        "reason": "worker and box in front",
        "options": ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"],
        "frames": ["frame1", "frame2"],
        "hint": "worker=1",
    }
    res = client.post("/api/v1/bridge/assist", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["caseId"] == "CASE-101"
    assert data["recommendation"] == "wait_for_removal"
    assert data["people_or_moving_equipment"] is True


def test_bridge_route_hazard(client):
    payload = {
        "hazardDescription": "forklift in finishing corner",
        "options": ["finishing_corner", "lane_c"],
    }
    res = client.post("/api/v1/bridge/route", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["route"] == "lane_c"

