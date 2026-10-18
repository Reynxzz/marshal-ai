"""The Three.js bridge: marshal_ai's flows answer the simulator, in the simulator's shapes.

A FakeLLM stands in for Gemini, so these check the wiring - what reaches the model, what is kept, what
goes back - and the rules enforced in code; the model's own quality is the eval's (marshal_ai.eval).
"""

import base64
import dataclasses

import pytest
from fastapi.testclient import TestClient

from marshal_ai.config import Settings
from marshal_ai.events import Topic
from marshal_ai.llm import FakeLLM, LLMError
from marshal_ai.records import AssistanceCase, Car, InspectionResult, Proposal, Verdict
from marshal_ai.runtime import build_runtime
from marshal_api.api.v1 import sim_bridge
from marshal_api.main import app as api

FRAME = base64.b64encode(b"\xff\xd8\xff\xe0 not really a jpeg").decode()
OPTIONS = ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"]
SETTINGS = Settings(bus="memory", store="memory", driving="fake", llm="offline",
                    decisions="track.decide,visual.screen,visual.match")


def decision(choice, p=0.97, other=None):
    return lambda call: {"choice": choice, "probs": {choice: p, **({other: 1 - p} if other else {})}}


def clean_car(llm: FakeLLM) -> FakeLLM:
    return (llm.on("visual.screen", decision("no defect visible", other="defect visible"))
               .on("visual.match", decision("matches", other="does not match")))


@pytest.fixture
def bridge():
    """The bridge on a FakeLLM; returns (client, flows, llm, published events)."""
    llm = FakeLLM()
    rt = build_runtime(SETTINGS, llm=llm)
    rt.bus = sim_bridge.StatusOnlyBus(rt.bus)
    flows = sim_bridge.BridgeFlows(rt)
    published: list[tuple[Topic, dict]] = []
    for topic in Topic:
        rt.bus.subscribe(topic, lambda data, topic=topic: published.append((topic, data)))
    api.dependency_overrides[sim_bridge.get_bridge_flows] = lambda: flows
    try:
        yield TestClient(api), flows, llm, published
    finally:
        api.dependency_overrides.pop(sim_bridge.get_bridge_flows, None)


def test_gate_pass_runs_the_visual_inspector(bridge):
    client, flows, llm, published = bridge
    clean_car(llm)
    res = client.post("/api/v1/bridge/gate", json={
        "vin": "P001", "expectedColour": "Blue", "frames": {v: FRAME for v in ("front", "rear", "left", "right")},
        "hints": {"front": "colour=red"}})
    assert res.status_code == 200
    assert res.json() == {"pass": True, "colour": "blue", "confidence": 0.9, "faults": [], "degraded": False}
    # four view screens and the build-sheet match, each on the simulator's frames; hints never sent
    assert sorted(c.task for c in llm.calls) == ["visual.match"] + ["visual.screen"] * 4
    assert all(m.data == base64.b64decode(FRAME) for c in llm.calls for m in c.media)
    assert not any("colour=red" in c.prompt for c in llm.calls)
    result = flows.rt.store.get(InspectionResult, "P001:visual")
    assert result.verdict == Verdict.PASS
    assert flows.rt.store.get(Car, "P001").build_sheet.colour == "blue"
    # a status line on vehicle-status only: the CARLA plant's subscriptions never see the car
    assert [(t, d["type"], d["level"]) for t, d in published] == [
        (Topic.VEHICLE_STATUS, "threejs.inspection-results", "good")]
    assert published[0][1]["record"]["verdict"] == "PASS"


def test_gate_damage_and_wrong_colour_fail_the_car(bridge):
    client, flows, llm, _ = bridge
    llm.on("visual.screen", lambda call: (
        {"choice": "defect visible", "probs": {"defect visible": 0.95, "no defect visible": 0.05}}
        if "front_left_door" in call.prompt else
        {"choice": "no defect visible", "probs": {"no defect visible": 0.97, "defect visible": 0.03}}))
    llm.on("visual.defects", lambda call: {"findings": [{"zone": "front_left_door", "type": "dent",
                                                         "confidence": 0.95, "observation": "a dent"}],
                                           "clear_confidence": 0.9})
    llm.on("visual.match", decision("does not match", other="matches"))
    llm.on("visual.variant", lambda call: {"observed_colour": "red", "confidence": 0.95, "mismatches": [
        {"item": "colour", "expected": "blue", "observed": "red", "confidence": 0.96}]})
    res = client.post("/api/v1/bridge/gate", json={
        "vin": "P003", "expectedColour": "blue", "frames": {"front": FRAME, "left": FRAME}})
    body = res.json()
    assert body["pass"] is False
    assert body["colour"] == "red"
    assert body["faults"] == ["colour_mismatch", "dent:left"]
    assert body["confidence"] == 0.96
    assert flows.rt.store.get(InspectionResult, "P003:visual").verdict == Verdict.FAIL


def test_gate_uncertain_car_is_held_for_review(bridge):
    client, flows, llm, _ = bridge
    llm.on("visual.screen", decision("no defect visible", p=0.7, other="defect visible"))
    llm.on("visual.defects", lambda call: {"findings": [], "clear_confidence": 0.7})
    llm.on("visual.match", decision("matches", other="does not match"))
    body = client.post("/api/v1/bridge/gate", json={
        "vin": "P002", "expectedColour": "white", "frames": {"front": FRAME}}).json()
    assert body["pass"] is False and body["faults"] == ["review_uncertain"]
    assert [p.kind for p in flows.rt.store.list(Proposal)] == ["review"]


def test_gate_without_gemini_is_503_not_a_fallback_verdict(bridge):
    client, _, llm, _ = bridge
    def down(call):
        raise LLMError("quota")
    for task in ("visual.screen", "visual.match", "visual.defects", "visual.variant"):
        llm.on(task, down)
    res = client.post("/api/v1/bridge/gate", json={"vin": "P001", "expectedColour": "blue",
                                                    "frames": {"front": FRAME}})
    assert res.status_code == 503


def test_gate_without_frames_is_left_to_the_simulator(bridge):
    client, _, llm, _ = bridge
    res = client.post("/api/v1/bridge/gate", json={"vin": "P001", "expectedColour": "blue",
                                                    "frames": {"front": ""}})
    assert res.status_code == 422
    assert llm.calls == []


def test_lamp_step_runs_the_functional_checker_and_keeps_every_step(bridge):
    client, flows, llm, published = bridge
    llm.on("track.decide", decision("yes", other="no"))
    body = client.post("/api/v1/bridge/lamp", json={
        "vin": "P001", "step": "headlights", "telemetry": {"headlights": "ON"},
        "frames": [FRAME, FRAME], "hint": "headlights=off"}).json()
    assert body == {"expected": "ON", "observed": "ON", "pass": True, "degraded": False}
    llm.on("track.decide", decision("no", other="yes"))
    body = client.post("/api/v1/bridge/lamp", json={
        "vin": "P001", "step": "brake_lights", "telemetry": {"brake_signal": True, "brake_lamps": "ON"},
        "frames": [FRAME]}).json()
    # telemetry says ON, the camera sees the lamp dark: the disagreement the check exists for
    assert body == {"expected": "ON", "observed": "OFF", "pass": False, "degraded": False}
    result = flows.rt.store.get(InspectionResult, "P001:functional")
    assert [(i.name, i.camera_ok) for i in result.checklist] == [("headlights", True), ("brake_lights", False)]
    assert result.verdict == Verdict.FAIL
    assert not any("headlights=off" in c.prompt for c in llm.calls)
    assert [(t, d["type"]) for t, d in published] == [(Topic.VEHICLE_STATUS, "threejs.inspection-results")] * 2


def test_lamp_unknown_step_is_rejected(bridge):
    client, *_ = bridge
    res = client.post("/api/v1/bridge/lamp", json={"vin": "P001", "step": "horn", "telemetry": {},
                                                    "frames": [FRAME]})
    assert res.status_code == 422


def test_assist_runs_the_desk_and_never_passes_a_person(bridge):
    client, flows, llm, published = bridge
    llm.on("assist.assess", lambda call: {
        "situation": "A box in the lane, a worker beside it.", "people_or_moving_equipment": False,
        "ranking": [{"option": "pass_left_3kmh", "reason": "room on the left", "risk": "low"}],
        "recommended": "pass_left_3kmh", "confidence": 0.9})
    llm.on("assist.locate", lambda call: {"objects": [
        {"label": "worker", "kind": "person", "frame": 1, "box_2d": [100, 200, 500, 400]}]})
    body = client.post("/api/v1/bridge/assist", json={
        "case_id": "AC-7", "vin": "P004", "reason": "unknown object in lane", "options": OPTIONS,
        "frames": [FRAME, FRAME]}).json()
    # the model missed the person it boxed; the desk's rules see it and only waiting is allowed
    assert body["recommendation"] == "wait_for_removal"
    assert body["boxes"] == [{"label": "worker", "x": 0.2, "y": 0.1, "w": 0.2, "h": 0.4}]
    assert body["description"] == "A box in the lane, a worker beside it."
    case = flows.rt.store.get(AssistanceCase, "AC-7")
    assert case.vin == "P004" and case.people_or_moving_equipment is True
    assert [len(c.media) for c in llm.calls] == [2, 2]
    assert flows.desk.frames == {}
    assert [(t, d["type"]) for t, d in published] == [(Topic.VEHICLE_STATUS, "assist.analysis")]


def test_assist_without_gemini_is_503(bridge):
    client, flows, llm, _ = bridge
    def down(call):
        raise LLMError("timeout")
    llm.on("assist.assess", down).on("assist.locate", down)
    res = client.post("/api/v1/bridge/assist", json={"case_id": "AC-8", "reason": "box", "options": OPTIONS,
                                                      "frames": [FRAME]})
    assert res.status_code == 503
    assert flows.desk.frames == {}


def test_route_is_a_decision_with_probabilities(bridge):
    client, _, llm, published = bridge
    llm.on("route.choose", lambda call: {"choice": "lane_c", "probs": {"lane_c": 0.93, "finishing_corner": 0.07}})
    body = client.post("/api/v1/bridge/route", json={
        "hazardDescription": "forklift working in the finishing corner",
        "options": ["finishing_corner", "lane_c"]}).json()
    assert body["route"] == "lane_c" and body["confidence"] == 0.93 and body["degraded"] is False
    assert "forklift working in the finishing corner" in llm.calls[0].prompt
    event = published[-1][1]
    assert event["type"] == "route.choice" and event["probs"] == {"lane_c": 0.93, "finishing_corner": 0.07}


def test_yard_moves_are_checked_against_the_layout(bridge):
    client, _, llm, _ = bridge
    layout = {"A1": None, "A2": "P001", "A3": None}
    llm.on("yard.replan", lambda call: {"moves": [{"vin": "P001", "from": "A2", "to": "A1"}],
                                        "reason": "frees A2 for the truck"})
    body = client.post("/api/v1/bridge/yard", json={"layout": layout,
                                                     "constraints": {"reservedSlots": ["A2"]}}).json()
    assert body == {"moves": [{"vin": "P001", "from": "A2", "to": "A1"}], "reason": "frees A2 for the truck",
                    "degraded": False}
    # a move onto a reserved slot never goes back to the simulator
    llm.on("yard.replan", lambda call: {"moves": [{"vin": "P001", "from": "A2", "to": "A3"}], "reason": "x"})
    res = client.post("/api/v1/bridge/yard", json={"layout": layout, "constraints": {"reservedSlots": ["A2", "A3"]}})
    assert res.status_code == 503


def test_offline_api_does_not_pretend_to_be_gemini(monkeypatch):
    monkeypatch.setattr(sim_bridge, "ai_settings", dataclasses.replace(SETTINGS, llm="offline"))
    monkeypatch.setattr(sim_bridge, "_flows", None)
    res = TestClient(api).post("/api/v1/bridge/route", json={"hazardDescription": "",
                                                              "options": ["finishing_corner", "lane_c"]})
    assert res.status_code == 503


def test_fell_back_ignores_screens_and_answered_hedges():
    assert sim_bridge.fell_back([("visual.screen", False), ("visual.defects", True)]) == []
    assert sim_bridge.fell_back([("visual.defects", False), ("visual.defects", True)]) == []
    assert sim_bridge.fell_back([("visual.defects", False)]) == ["visual.defects"]

