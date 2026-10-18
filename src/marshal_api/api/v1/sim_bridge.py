"""Bridge from the Three.js plant (marshal-fe) to marshal_ai's own Gemini flows.

The browser simulator sends its camera frames and telemetry here; each endpoint runs the same flow the
CARLA plant runs and answers in the simulator's shapes (src/lib/marshal/types.ts in marshal-fe):

    POST /bridge/gate    VisualInspector.inspect   decision screens, defects, build-sheet match, references
    POST /bridge/lamp    FunctionalChecker          camera vs telemetry, against a passed car's frame
    POST /bridge/assist  AssistanceDesk             assessment + boxes, the desk's rules enforced in code
    POST /bridge/route   route.choose decision      one token, a probability per way
    POST /bridge/yard    yard re-plan               moves checked against the layout before they go back

Records go through marshal_ai's store (Firestore with MARSHAL_STORE=gcp, every put also a BigQuery
history row with MARSHAL_HISTORY=bq), evidence frames through its artifacts (GCS with
MARSHAL_ARTIFACTS=gcs), and each result is published on the API's event bus (Pub/Sub with
MARSHAL_BUS=gcp), so the Control Tower and /events see the simulator's cars like the CARLA ones.

Parking is not bridged: its candidate paths come from the simulator's own A* (marshal-fe
src/lib/marshal/parking/astar.ts), so the simulator asks Gemini for it itself.

There is no rule-based answer here. Without Gemini (MARSHAL_LLM=offline), or when a call fails, an
endpoint answers 503 and the simulator falls back to its own deterministic offline answer, which it
marks degraded - an answer from this bridge is always Gemini's.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import dataclasses
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from marshal_ai.config import settings as ai_settings
from marshal_ai.driving import AssistRequest
from marshal_ai.events import EventBus, Topic, make_bus
from marshal_ai.fakeworld import GATE_VIEWS
from marshal_ai.flows import route as route_flow
from marshal_ai.flows.assistance import AssistanceDesk
from marshal_ai.flows.base import ask
from marshal_ai.flows.functional import ROUTINE, FunctionalChecker
from marshal_ai.flows.visual import PASS_CONFIDENCE, VisualInspector
from marshal_ai.llm import Media, decision_schema
from marshal_ai.records import BuildSheet, Car, CarState, InspectionResult, Verdict
from marshal_ai.driving.routines import routine_steps
from marshal_ai.runtime import Runtime, build_runtime
from marshal_api.bridge import get_event_bus
from marshal_api.config import get_settings

log = logging.getLogger("marshal.bridge.threejs")

router = APIRouter()

VIEWS = ("front", "rear", "left", "right")
# The simulator's car; the build sheet's model is part of the gate's match check.
SIM_MODEL = "sedan"
# Where the simulator's evidence goes in the bucket, apart from the CARLA plant's.
EVIDENCE_PREFIX = "gs://marshal/threejs"


# -------------------------------------------------------------------------------------------------
# Requests (marshal-fe src/lib/marshal/schemas.ts; size limits are enforced there)
# -------------------------------------------------------------------------------------------------

class GateInputPayload(BaseModel):
    vin: str
    expectedColour: str
    frames: dict[str, str | None]
    # the simulator's offline truth; never sent to Gemini
    hints: dict[str, str] | None = None


class LampInputPayload(BaseModel):
    vin: str
    step: str
    telemetry: dict[str, Any]
    frames: list[str] = Field(default_factory=list)
    hint: str | None = None


class AssistInputPayload(BaseModel):
    case_id: str
    vin: str | None = None
    reason: str
    options: list[str]
    frames: list[str] = Field(default_factory=list)
    hint: str | None = None


class RouteInputPayload(BaseModel):
    hazardDescription: str
    options: list[str]


class YardInputPayload(BaseModel):
    layout: dict[str, str | None]
    constraints: dict[str, Any] = Field(default_factory=dict)


class GenericAiTaskPayload(BaseModel):
    task: str
    payload: dict[str, Any] = Field(default_factory=dict)


# -------------------------------------------------------------------------------------------------
# The flows behind the bridge
# -------------------------------------------------------------------------------------------------

class _FrameDesk(AssistanceDesk):
    """The assistance desk, reading the clip the simulator sent instead of a driving system's."""

    def __init__(self, app):
        super().__init__(app)
        self.frames: dict[str, list[Media]] = {}

    def clip(self, request: AssistRequest) -> list[Media]:
        return self.frames.pop(request.case_id, [])


# The Gemini calls made during this request, as (task, answered). The flows fall back to a safe answer
# when a call fails (a REVIEW, a wait); the bridge answers 503 instead, so the simulator's own fallback
# is used and is marked degraded. asyncio.to_thread and gather copy the context, so the list is shared.
_calls: ContextVar[list[tuple[str, bool]] | None] = ContextVar("bridge_calls", default=None)

# Decision screens: when one fails the flow asks the same question in full, so it is not a fallback.
RETRIED_IN_FULL = ("visual.screen", "visual.match", "track.decide")


class _Watched:
    """The runtime's LLM, noting each call and whether it answered in the request's `_calls`."""

    def __init__(self, llm):
        self.llm = llm

    def __getattr__(self, name):          # handlers etc., as flows.base.use_decisions reads them
        return getattr(self.llm, name)

    def generate(self, task, prompt, schema=None, media=None, system=""):
        calls = _calls.get()
        try:
            out = self.llm.generate(task, prompt, schema, media, system)
        except Exception:
            if calls is not None:
                calls.append((task, False))
            raise
        if calls is not None:
            calls.append((task, True))
        return out


def fell_back(calls: list[tuple[str, bool]]) -> list[str]:
    """Tasks with more failed calls than answered ones. The gate asks a slow view twice (flows.base
    hedging) and takes whichever answers, so one failure beside an answer is not a fallback."""
    net: dict[str, int] = {}
    for task, answered in calls:
        if task not in RETRIED_IN_FULL:
            net[task] = net.get(task, 0) + (-1 if answered else 1)
    return sorted(task for task, n in net.items() if n > 0)


@contextmanager
def gemini_only(name: str):
    """Runs a flow; 503 when it had to fall back on a failed Gemini call."""
    calls: list[tuple[str, bool]] = []
    token = _calls.set(calls)
    try:
        yield
    except HTTPException:
        raise
    except Exception as exc:
        raise _unavailable(name, exc) from exc
    finally:
        _calls.reset(token)
    failed = fell_back(calls)
    if failed:
        log.warning("%s: Gemini failed on %s; the simulator answers offline", name, failed)
        raise HTTPException(503, f"{name}: Gemini unavailable ({', '.join(failed)})")


class StatusOnlyBus:
    """The API bus, carrying the bridge's results as status lines on vehicle-status only.

    On Pub/Sub the topics are shared with the CARLA plant, whose subscriptions act on what arrives:
    its Report Router would take a simulator car's inspection result for one of its own. The plant
    reads vehicle-status for mission.done alone, so a status line there reaches the Control Tower's
    /events and the dashboards and nothing else.
    """

    LEVEL = {"pass": "good", "review": "warn", "fail": "bad"}

    def __init__(self, bus: EventBus):
        self.bus = bus

    def __getattr__(self, name):
        return getattr(self.bus, name)

    async def publish(self, topic: Topic, message) -> None:
        data = message.model_dump(mode="json") if isinstance(message, BaseModel) else dict(message)
        if topic is not Topic.VEHICLE_STATUS:
            verdict = str(data.get("verdict", ""))
            data = {"type": f"threejs.{topic.value}", "flow": 3 if data.get("kind") == "visual" else 4,
                    "source": "threejs", "vin": data.get("vin"),
                    "title": f"{data.get('vin', '?')} {data.get('kind', topic.value)} {verdict.upper()}",
                    "detail": "; ".join(data.get("observations") or [])[:300],
                    "level": self.LEVEL.get(verdict.lower(), "info"), "record": data}
        await self.bus.publish(Topic.VEHICLE_STATUS, data)


class BridgeFlows:
    """marshal_ai's flows on a runtime of their own: Gemini, store, artifacts, bus.

    Only the flow objects are built, not a MarshalApp: an app subscribes to the plant's topics, and on
    Pub/Sub it would then also act on the CARLA plant's events. These flows only publish.
    """

    def __init__(self, rt: Runtime):
        if not isinstance(rt.llm, _Watched):
            rt.llm = _Watched(rt.llm)
        self.rt = rt
        shell = SimpleNamespace(rt=rt)
        # each keeps the latest passed car per camera and step: the reference the next is compared to
        self.visual = VisualInspector(shell)
        self.functional = FunctionalChecker(shell)
        self.desk = _FrameDesk(shell)


_flows: BridgeFlows | None = None


def build_bridge_flows() -> BridgeFlows:
    """The bridge's runtime from marshal_ai's settings, on the fake driving system (nothing is driven)."""
    s = dataclasses.replace(ai_settings, driving="fake", parking="standin")
    if s.llm != "gemini":
        raise HTTPException(503, "the bridge answers with Gemini only (MARSHAL_LLM=gemini)")
    # The in-process demo plant (MARSHAL_RUN_PLANT=1) is subscribed to the API's bus and would take the
    # simulator's results for its own cars, so the bridge then publishes on a bus of its own.
    bus = make_bus("memory") if get_settings().run_plant else get_event_bus()
    return BridgeFlows(build_runtime(s, bus=StatusOnlyBus(bus)))


def get_bridge_flows() -> BridgeFlows:
    global _flows
    if _flows is None:
        _flows = build_bridge_flows()
    return _flows


def _frame(data: str) -> bytes:
    """A base64 JPEG (optionally a data: URL) as bytes."""
    if data.startswith("data:"):
        data = data.split(",", 1)[-1]
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, f"frame is not base64: {exc}") from exc


async def _evidence(rt: Runtime, uri: str, data: bytes) -> str:
    """The frame in the artifacts store (GCS with MARSHAL_ARTIFACTS=gcs); its URI either way."""
    try:
        return await asyncio.to_thread(rt.artifacts.put, uri, data, "image/jpeg")
    except Exception as exc:          # the verdict does not depend on the upload
        log.warning("evidence upload failed for %s: %s: %s", uri, type(exc).__name__, exc)
        return uri


def _ensure_car(rt: Runtime, vin: str, colour: str | None = None) -> Car:
    """The simulator's car in the store, created on first sight; a new build-sheet colour is kept."""
    car = rt.store.get(Car, vin)
    if car is None:
        car = Car(vin=vin, build_sheet=BuildSheet(model=SIM_MODEL, colour=colour or "unknown"),
                  state=CarState.INSPECTION_GATE, location="inspection_gate", updated_at=rt.clock())
        rt.store.put(car)
    elif colour and car.build_sheet.colour != colour:
        car = car.model_copy(update={"build_sheet": car.build_sheet.model_copy(update={"colour": colour}),
                                     "updated_at": rt.clock()})
        rt.store.put(car)
    return car


async def _status(rt: Runtime, kind: str, flow: int, title: str, detail: str, **extra) -> None:
    """A line for the dashboards and /events, in the shape flows/route.py announces decisions in."""
    try:
        await rt.bus.publish(Topic.VEHICLE_STATUS, {"type": kind, "flow": flow, "source": "threejs",
                                                    "title": title, "detail": detail, "level": "info",
                                                    **extra})
    except Exception as exc:
        log.warning("publish %s failed: %s: %s", kind, type(exc).__name__, exc)


def _unavailable(task: str, exc: Exception) -> HTTPException:
    log.warning("%s: Gemini unavailable (%s: %s)", task, type(exc).__name__, str(exc)[:200])
    return HTTPException(503, f"{task}: Gemini unavailable ({type(exc).__name__})")


# -------------------------------------------------------------------------------------------------
# Gate (flow 3)
# -------------------------------------------------------------------------------------------------

_SHOWS = re.compile(r"car shows (?P<seen>.+?) \(\d")


def gate_answer(result: InspectionResult, expected: str) -> dict[str, Any]:
    """An InspectionResult as the simulator's GateResult.

    Faults read `type:view` ("dent:left"), and "colour_mismatch" for the colour. A REVIEW is not a
    pass: the simulator holds the car, as the gate holds it for a person. Confidence is the strongest
    finding's on a failure, and the bar every view cleared on a pass.
    """
    view_of = {zone: view for view, zones in GATE_VIEWS.items() for zone in zones}
    faults = [f"{f.type}:{view_of.get(f.zone, f.zone)}" for f in result.findings]
    colour = expected
    for m in result.mismatch_findings:
        if m.type == "wrong_colour":
            faults.insert(0, "colour_mismatch")
            seen = _SHOWS.search(m.observation or "")
            colour = seen["seen"].strip().lower() if seen else "unknown"
        else:
            faults.append(m.type)
    scores = [f.confidence for f in result.findings + result.mismatch_findings]
    if result.verdict == Verdict.REVIEW and not faults:
        faults.append("review_uncertain")
    return {
        "pass": result.verdict == Verdict.PASS,
        "colour": colour,
        "confidence": round(max(scores) if scores else PASS_CONFIDENCE, 3),
        "faults": faults,
        "degraded": False,
    }


@router.post("/gate")
async def evaluate_gate_photos(data: GateInputPayload,
                               flows: BridgeFlows = Depends(get_bridge_flows)) -> dict[str, Any]:
    """The gate check on the simulator's four views (VisualInspector.inspect)."""
    rt = flows.rt
    expected = data.expectedColour.strip().lower()
    frames = {view: _frame(f) for view, f in data.frames.items() if view in VIEWS and f}
    if not frames:
        raise HTTPException(422, "no gate frames: the simulator answers this one offline")
    _ensure_car(rt, data.vin, expected)
    uris = await asyncio.gather(*(_evidence(rt, f"{EVIDENCE_PREFIX}/gate/{data.vin}/{view}.jpg", raw)
                                  for view, raw in frames.items()))
    images = [Media(mime="image/jpeg", data=raw, uri=uri, label=f"{view} gate camera")
              for (view, raw), uri in zip(frames.items(), uris)]
    with gemini_only("gate"):
        result = await flows.visual.inspect(data.vin, images=images, check_variant=True)
    return gate_answer(result, expected)


# -------------------------------------------------------------------------------------------------
# Lamp check (flow 4)
# -------------------------------------------------------------------------------------------------

def _record_step(rt: Runtime, vin: str, item) -> InspectionResult:
    """The step in the car's functional record, replacing an earlier answer for the same step.

    The simulator asks one step at a time, so the verdict is of the steps asked so far; the car's
    state is the simulator's to move.
    """
    previous = rt.store.get(InspectionResult, f"{vin}:functional")
    items = [i for i in (previous.checklist if previous else []) if i.step != item.step] + [item]
    items.sort(key=lambda i: i.step)
    if any(i.telemetry_ok is False or i.camera_ok is False for i in items):
        verdict = Verdict.FAIL
    elif any(i.camera_ok is None and i.camera_applicable for i in items):
        verdict = Verdict.REVIEW
    else:
        verdict = Verdict.PASS
    result = InspectionResult(vin=vin, kind="functional", verdict=verdict, checklist=items,
                              created_at=rt.clock())
    rt.store.put(result)
    return result


@router.post("/lamp")
async def evaluate_lamp_evidence(data: LampInputPayload,
                                 flows: BridgeFlows = Depends(get_bridge_flows)) -> dict[str, Any]:
    """One R-12 lamp step from the simulator's evidence frames (FunctionalChecker.judge_clip)."""
    rt = flows.rt
    steps = {s.name: s for s in routine_steps(ROUTINE)}
    step = steps.get(data.step.strip().lower())
    if step is None:
        raise HTTPException(422, f"unknown {ROUTINE} step {data.step!r}")
    frames = [f for f in data.frames if f]
    if not frames:
        raise HTTPException(422, "no evidence frames: the simulator answers this one offline")
    # the newest frame: the lamp steps judged here are confirmable from a still (routines.py)
    raw = _frame(frames[-1])
    _ensure_car(rt, data.vin)
    uri = await _evidence(rt, f"{EVIDENCE_PREFIX}/track/{data.vin}/step{step.step}_{step.name}.jpg", raw)
    clip = Media(mime="image/jpeg", data=raw, uri=uri, label="track camera")
    with gemini_only("lamp"):
        item = await flows.functional.judge_clip(data.vin, step.step, clip, dict(data.telemetry))
    item = item.model_copy(update={"telemetry": dict(data.telemetry)})
    result = _record_step(rt, data.vin, item)
    await rt.bus.publish(Topic.INSPECTION_RESULTS, result)
    observed = {True: "ON", False: "OFF", None: "UNVERIFIED"}[item.camera_ok]
    return {
        "expected": "ON" if item.telemetry_ok else "OFF",
        "observed": observed,
        "pass": bool(item.telemetry_ok and item.camera_ok),
        "degraded": False,
    }


# -------------------------------------------------------------------------------------------------
# Remote assistance (flow 2)
# -------------------------------------------------------------------------------------------------

def assist_answer(case, options: list[str]) -> dict[str, Any]:
    """An AssistanceCase as the simulator's AssistAnalysis; boxes 0-1000 [ymin, xmin, ymax, xmax] to 0-1."""
    boxes = []
    for o in case.objects:
        box = o.get("box_2d")
        if not box:
            continue
        ymin, xmin, ymax, xmax = (v / 1000 for v in box)
        boxes.append({"label": o.get("label", ""), "x": xmin, "y": ymin, "w": xmax - xmin, "h": ymax - ymin})
    recommendation = case.recommended
    if recommendation not in options:
        # the desk recommends nothing when every option is unsafe: the car stays stopped
        recommendation = "wait_for_removal" if "wait_for_removal" in options else options[0]
    return {"description": case.situation, "boxes": boxes, "recommendation": recommendation,
            "degraded": False}


@router.post("/assist")
async def analyze_assist_clip(data: AssistInputPayload,
                              flows: BridgeFlows = Depends(get_bridge_flows)) -> dict[str, Any]:
    """A stopped car's clip at the assistance desk (AssistanceDesk.on_request)."""
    rt = flows.rt
    if not data.options:
        raise HTTPException(422, "no options offered")
    raws = [_frame(f) for f in data.frames if f]
    uris = await asyncio.gather(*(_evidence(rt, f"{EVIDENCE_PREFIX}/assist/{data.case_id}_f{i}.jpg", raw)
                                  for i, raw in enumerate(raws)))
    flows.desk.frames[data.case_id] = [Media(mime="image/jpeg", data=raw, uri=uri, label=f"frame {i}")
                                       for i, (raw, uri) in enumerate(zip(raws, uris))]
    vin = data.vin or "unknown"
    request = AssistRequest(case_id=data.case_id, vin=vin, location="threejs", reason=data.reason,
                            options=data.options, clip_uri=uris[-1] if uris else None)
    try:
        with gemini_only("assist"):
            case = await flows.desk.on_request(request)
    finally:
        flows.desk.frames.pop(data.case_id, None)
    answer = assist_answer(case, data.options)
    await _status(rt, "assist.analysis", 2, f"{vin} {data.case_id} → {answer['recommendation']}",
                  case.situation, vin=vin, case_id=data.case_id, recommended=answer["recommendation"],
                  people_or_moving_equipment=case.people_or_moving_equipment)
    return answer


# -------------------------------------------------------------------------------------------------
# Route choice (flow 1)
# -------------------------------------------------------------------------------------------------

# What each of the simulator's two ways from the report point is (marshal_ai flows/route.py).
WAYS = {
    "finishing_corner": "the shorter way, through the finishing corner, a blind corner the forklifts use",
    "lane_c": "along lane C, about 13 m longer",
}


def route_prompt(hazard: str, options: list[str]) -> str:
    lines = ["A car drives from report_point towards the yard. The ways it can take:"]
    for option in options:
        hazards = hazard if option == "finishing_corner" and hazard else "none reported"
        lines.append(f"- {option}: {WAYS.get(option, option)}; hazards: {hazards}")
    lines += ["", "Which way should it take?"]
    return "\n".join(lines)


@router.post("/route")
async def choose_route_hazard(data: RouteInputPayload,
                              flows: BridgeFlows = Depends(get_bridge_flows)) -> dict[str, Any]:
    """The way from the report point, as a decision (route.choose: one token, a probability per way)."""
    rt = flows.rt
    options = list(dict.fromkeys(data.options))
    if not options:
        raise HTTPException(422, "no route options")
    hazard = data.hazardDescription.strip()
    if len(options) == 1:
        return {"route": options[0], "confidence": 1.0, "reason": "the only way offered", "degraded": False}
    started = time.monotonic()
    with gemini_only("route"):
        decision = await ask(rt, route_flow.TASK, route_prompt(hazard, options),
                             decision_schema(*options), system=route_flow.SYSTEM)
    seconds = round(time.monotonic() - started, 2)
    probs = {k: round(v, 3) for k, v in decision.probs.items()}
    reason = (f"Gemini: {decision.choice} (p={decision.confidence:.2f})"
              + (f"; hazard reported: {hazard}" if hazard else "; nothing reported on either way"))
    await route_flow.announce(rt, {"vin": "threejs", "choice": decision.choice, "by": "gemini",
                                   "probs": probs, "why": reason, "seconds": seconds})
    return {"route": decision.choice, "confidence": round(decision.confidence, 3), "reason": reason,
            "degraded": False}


# -------------------------------------------------------------------------------------------------
# Yard re-plan (flow 1)
# -------------------------------------------------------------------------------------------------

class YardMove(BaseModel):
    vin: str
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


class YardReplanAnswer(BaseModel):
    moves: list[YardMove]
    reason: str = Field(description="one sentence")


YARD_SYSTEM = """You re-plan a finished-car yard. Vacate reserved slots and bring priority cars to the
lowest free slots, with as few moves as possible. Moves run in order; a move takes a car from the slot
it is in to a slot that is free at that moment and not reserved. Never invent a car or a slot."""


def valid_moves(layout: dict[str, str | None], reserved: set[str], moves: list[YardMove]) -> bool:
    """The moves replayed on the layout: each from the car's slot to a free, unreserved slot."""
    layout = dict(layout)
    for m in moves:
        if m.from_ not in layout or m.to not in layout:
            return False
        if layout[m.from_] != m.vin or layout[m.to] is not None or m.to in reserved:
            return False
        layout[m.to], layout[m.from_] = m.vin, None
    return True


@router.post("/yard")
async def replan_yard_slots(data: YardInputPayload,
                            flows: BridgeFlows = Depends(get_bridge_flows)) -> dict[str, Any]:
    """Slot moves for reserved slots and priority cars; checked in code before they go back."""
    rt = flows.rt
    reserved = set(data.constraints.get("reservedSlots") or [])
    prompt = (f"Layout (slot -> car, null = free): {data.layout}\n"
              f"Reserved slots: {sorted(reserved)}\n"
              f"Priority cars: {data.constraints.get('priorityVins') or []}\n"
              f"Why: {data.constraints.get('reason') or 'not given'}")
    with gemini_only("yard"):
        plan = await ask(rt, "yard.replan", prompt, YardReplanAnswer, system=YARD_SYSTEM)
    if not valid_moves(data.layout, reserved, plan.moves):
        # a move onto a taken or reserved slot never reaches the yard: the simulator plans it offline
        raise HTTPException(503, "yard: Gemini's moves do not fit the layout")
    moves = [{"vin": m.vin, "from": m.from_, "to": m.to} for m in plan.moves]
    await _status(rt, "yard.replan", 1, f"yard re-plan: {len(moves)} move(s)", plan.reason, moves=moves)
    return {"moves": moves, "reason": plan.reason.strip() or "Gemini yard re-plan", "degraded": False}


# -------------------------------------------------------------------------------------------------
# Batch tasks
# -------------------------------------------------------------------------------------------------

@router.post("/ai-task")
async def execute_generic_ai_task(data: GenericAiTaskPayload) -> dict[str, Any]:
    """Not bridged yet: the simulator's batch tasks (src/ai) call Gemini themselves."""
    raise HTTPException(501, f"{data.task}: not bridged; the simulator answers it itself")
