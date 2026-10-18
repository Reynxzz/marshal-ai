"""The dashboard is the only place flows 5 and 6 are visible, so its wiring is tested."""

from __future__ import annotations

import json
import threading
import time

import pytest

from marshal_ai.dashboard import FLOWS, EventLog, attach, describe, parser, snapshot
from marshal_ai.events import Topic


def test_every_topic_is_mapped_to_a_flow():
    """A topic with no mapping would silently vanish from the feed."""
    assert set(FLOWS) == set(Topic), f"unmapped topics: {set(Topic) - set(FLOWS)}"
    assert sorted(flow for flow, _ in FLOWS.values()) == [1, 2, 3, 4, 5, 6, 7]


def test_event_log_returns_only_newer_events():
    log = EventLog()
    log.publish(1, "yard", "first")
    second = log.publish(2, "desk", "second")

    assert [e.title for e in log.since(0)] == ["first", "second"]
    assert [e.title for e in log.since(1)] == ["second"]
    assert log.latest_seq == second.seq


def test_event_log_is_bounded():
    log = EventLog(maxlen=3)
    for i in range(6):
        log.publish(1, "yard", f"e{i}")

    assert [e.title for e in log.since(0)] == ["e3", "e4", "e5"]


def test_event_log_wakes_a_waiting_reader():
    log = EventLog()

    def publish_soon():
        time.sleep(0.02)
        log.publish(7, "lab", "late arrival")

    threading.Thread(target=publish_soon, daemon=True).start()
    batch = log.wait_after(0, timeout=2.0)

    assert [e.title for e in batch] == ["late arrival"]


def test_wait_after_returns_empty_on_timeout_so_keepalives_can_be_sent():
    assert EventLog().wait_after(0, timeout=0.01) == []


@pytest.mark.parametrize(("topic", "data", "level", "in_title"), [
    (Topic.VEHICLE_STATUS, {"type": "mission.done", "vin": "V1", "location": "yard.Y1"},
     "good", "arrived"),
    (Topic.ASSIST_REQUESTS, {"vin": "V1", "reason": "box in lane", "options": [{"id": "wait"}]},
     "warn", "asked for help"),
    (Topic.INSPECTION_RESULTS, {"vin": "V1", "verdict": "FAIL", "findings": [{"zone": "door"}]},
     "bad", "FAIL"),
    (Topic.INSPECTION_RESULTS, {"vin": "V1", "verdict": "PASS"}, "good", "PASS"),
    (Topic.TEST_RESULTS, {"variant": "SC-0142-v3", "passed": False,
                          "failed_criteria": ["no_contact"]}, "bad", "FAILED"),
    (Topic.TEST_RESULTS, {"variant": "SC-0142", "passed": True}, "good", "passed"),
    (Topic.VEHICLE_STATUS, {"type": "route.choice", "vin": "V1", "choice": "via lane_c",
                            "by": "gemini", "probs": {"via lane_c": 1.0}}, "info", "via lane_c"),
    (Topic.VEHICLE_STATUS, {"type": "parking.step", "vin": "V1", "slot": "Y4", "choice": "path_A",
                            "probs": {"path_A": 0.99}}, "good", "path_A"),
])
def test_describe_labels_outcomes(topic, data, level, in_title):
    title, _detail, got_level = describe(topic, data)

    assert got_level == level
    assert in_title in title


def test_describe_never_raises_on_an_unexpected_payload():
    title, detail, level = describe(Topic.GATE_IMAGES, {})

    assert isinstance(title, str)
    assert isinstance(detail, str)
    assert level in {"info", "good", "warn", "bad"}


def test_attach_subscribes_all_seven_flows_and_survives_bad_payloads(monkeypatch):
    class FakeBus:
        def __init__(self):
            self.handlers: dict[Topic, list] = {}

        def subscribe(self, topic, handler):
            self.handlers.setdefault(topic, []).append(handler)

    class FakeApp:
        def __init__(self):
            self.rt = type("RT", (), {"bus": FakeBus()})()

    app = FakeApp()
    events = EventLog()
    attach(app, events)

    assert set(app.rt.bus.handlers) == set(Topic)

    # a payload that cannot be described must still produce an event, not an exception
    for topic, handlers in app.rt.bus.handlers.items():
        for handler in handlers:
            handler({"type": "surprise"})
    assert len(events.since(0)) == len(Topic)


def test_repeated_vehicle_status_is_suppressed_but_changes_get_through():
    """Status arrives every tick; an unfiltered feed is unreadable."""
    class FakeBus:
        def __init__(self):
            self.handlers: dict[Topic, list] = {}

        def subscribe(self, topic, handler):
            self.handlers.setdefault(topic, []).append(handler)

    app = type("App", (), {"rt": type("RT", (), {"bus": FakeBus()})()})()
    events = EventLog()
    attach(app, events)
    [handler] = app.rt.bus.handlers[Topic.VEHICLE_STATUS]

    same = {"type": "status", "vin": "V1", "location": "line_end", "speed_kmh": 0.0, "progress": 0.0}
    for _ in range(5):
        handler(dict(same))
    assert len(events.since(0)) == 1, "identical status lines should collapse"

    handler({**same, "location": "inspection_gate"})
    handler({**same, "speed_kmh": 9.0})
    handler({"type": "mission.done", "vin": "V1", "location": "yard.Y1"})
    titles = [e.title for e in events.since(0)]

    assert any("inspection_gate" in t for t in titles)
    assert any("arrived" in t for t in titles), "mission.done must never be suppressed"


def test_repeated_decisions_are_never_suppressed():
    """A decision is worth a line every time, even when the same car decides twice in a row."""
    class FakeBus:
        def __init__(self):
            self.handlers: dict[Topic, list] = {}

        def subscribe(self, topic, handler):
            self.handlers.setdefault(topic, []).append(handler)

    app = type("App", (), {"rt": type("RT", (), {"bus": FakeBus()})()})()
    events = EventLog()
    attach(app, events)
    [handler] = app.rt.bus.handlers[Topic.VEHICLE_STATUS]

    decision = {"type": "route.choice", "vin": "V1", "choice": "via lane_c", "probs": {}}
    handler(dict(decision))
    handler(dict(decision))

    assert len(events.since(0)) == 2


def test_snapshot_tolerates_a_store_that_cannot_list(monkeypatch):
    class Boom:
        def list(self, _model):
            raise RuntimeError("firestore down")

        def pending(self):
            return []

    class FakeApp:
        def __init__(self):
            self.rt = type("RT", (), {"store": Boom(), "pending": lambda self=None: []})()

        def kpis(self):
            raise RuntimeError("no kpis")

    state = snapshot(FakeApp())

    assert state["cars"] == [] and state["runs"] == []
    assert "error" in state["kpis"]
    json.dumps(state, default=str)          # must stay serialisable for /state


def test_dashboard_defaults_are_loopback_only():
    args = parser().parse_args([])

    assert args.bind == "127.0.0.1"
    assert args.http_port == 8090
    assert args.camera_url.startswith("http://127.0.0.1:8080")


# --------------------------------------------------------------------------- #
# The approval pane: a supervisor decides on the page, through the flows' own calls
# --------------------------------------------------------------------------- #

async def test_open_case_is_on_the_page_and_a_supervisor_resolves_it(plant):
    from marshal_ai.dashboard import apply_decision, open_cases
    from marshal_ai.demo import BOX_SCENE
    from marshal_ai.driving import MissionGo
    from marshal_ai.records import AssistanceCase

    app = plant(["VIN-240"])
    await app.rt.missions.send(MissionGo(mission_id="M-1", vin="VIN-240", waypoints=["yard.Y1"]))
    await app.run_for(10)
    app.rt.world.scenes["AC-1"] = BOX_SCENE
    await app.rt.driving.raise_assist("VIN-240", "AC-1", "unknown object in lane",
                                      ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"],
                                      clip_uri="gs://marshal/assist/AC-1.mp4")

    pane = open_cases(app)
    assert [c["case_id"] for c in pane] == ["AC-1"]
    assert pane[0]["recommended"] == "wait_for_removal" and pane[0]["options"]
    json.dumps(pane, default=str)

    result = await apply_decision(app, {"case_id": "AC-1", "choice": "wait_for_removal", "by": "supervisor-ana"})

    assert result == {"case_id": "AC-1", "choice": "wait_for_removal", "resolved_by": "supervisor-ana"}
    assert app.rt.store.get(AssistanceCase, "AC-1").resolved_by == "supervisor-ana"
    assert open_cases(app) == []


async def test_a_decision_needs_a_name_and_a_known_proposal(app):
    from marshal_ai.dashboard import apply_decision

    with pytest.raises(ValueError):
        await apply_decision(app, {"case_id": "AC-1", "choice": "wait_for_removal", "by": ""})
    with pytest.raises(KeyError):
        await apply_decision(app, {"proposal_id": "P-404", "approve": True, "by": "supervisor-ana"})


async def test_a_pending_proposal_goes_to_the_flow_that_owns_its_kind(app, monkeypatch):
    from marshal_ai.dashboard import apply_decision

    p = app.rt.propose("replan", "move VIN-P1 to Y6", {"kind": "late_truck", "plan": {}})
    seen = {}

    async def yard_decide(pid, approve, by):
        seen.update(pid=pid, approve=approve, by=by)
        return {"proposal_id": pid, "status": "approved"}

    monkeypatch.setattr(app.yard, "decide", yard_decide)
    result = await apply_decision(app, {"proposal_id": p.proposal_id, "approve": True, "by": "supervisor-ana"})

    assert seen == {"pid": p.proposal_id, "approve": True, "by": "supervisor-ana"}
    assert result["status"] == "approved"


def test_dashboard_manual_mode_flag():
    assert parser().parse_args(["--demo", "--manual"]).manual
