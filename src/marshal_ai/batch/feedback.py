"""Capped batch digest and structured seven-flow operational feedback."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


FLOW_NAMES = {
    1: "Yard traffic",
    2: "Assistance",
    3: "Visual inspection",
    4: "Functional check",
    5: "Report and rework",
    6: "Scenario generator",
    7: "Adversarial tester",
}


class FlowFeedback(BaseModel):
    flow: int = Field(ge=1, le=7)
    name: str
    observation: str
    issues: list[str] = Field(default_factory=list)
    recommendation: str = ""


class BatchFeedback(BaseModel):
    batch_size: int = Field(ge=0)
    summary: str
    per_flow: list[FlowFeedback]
    top_issues: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    risk: str = "low"
    degraded: bool = False
    error: str | None = None


SYSTEM = """You review one simulated batch of finished cars in a car plant. Use only facts in the
batch record; never invent cars, defects, or decisions. Be concrete, cite VINs and flow numbers,
separate observations from recommendations, mention batch size, and return feedback for all seven
flows. If there was no incident, say flows 6 and 7 were not triggered."""


def _event_line(event: dict[str, Any]) -> str:
    flow = event.get("flow")
    flow_label = f"flow {flow}" if flow is not None else "system"
    return (f"[{event.get('seq', 0)}] {flow_label} · {event.get('source', '')} · "
            f"{event.get('title', '')} {event.get('detail', '')}").strip()


def build_digest(batch: dict, cars: list[dict], events: list[dict], result: dict, lab: dict) -> dict:
    ordered = sorted(events, key=lambda e: int(e.get("seq", 0)))
    priority = [e for e in ordered if e.get("level") in ("warn", "bad")]
    priority += [e for e in ordered if e.get("flow") in (1, 2, 3, 4, 5)]
    priority += [e for e in ordered if e.get("flow") in (6, 7)]
    tail = ordered[-50:]
    picked = {int(e.get("seq", i)): e for i, e in enumerate(priority + tail)}
    selected = sorted(picked.values(), key=lambda e: int(e.get("seq", 0)))[-200:]
    return {
        "batch_id": batch["batch_id"],
        "requested_cars": batch["requested_cars"],
        "created_cars": batch["created_cars"],
        "finished_cars": batch["finished_cars"],
        "engine": batch.get("engine", "carla"),
        "incident_enabled": batch.get("incident_enabled", False),
        "counts": batch.get("counts", {}),
        "flow_counts": batch.get("flow_counts", {}),
        "cars": cars,
        "labels": result.get("labels", {}),
        "lab": lab,
        "log_events": len(events),
        "exceptions": sum(e.get("level") in ("warn", "bad") for e in events),
        "logs": [_event_line(e) for e in selected],
    }


def build_prompt(digest: dict) -> str:
    record = {key: value for key, value in digest.items() if key != "logs"}
    return (
        "Review this batch record. Return a concise, evidence-based assessment.\n\n"
        "Batch record JSON:\n"
        f"{json.dumps(record, indent=2, default=str)}\n\n"
        "Logs (oldest first, capped):\n"
        + "\n".join(digest.get("logs", []))
    )


def offline_feedback(digest: dict) -> dict:
    labels = digest.get("labels", {})
    flow_counts = digest.get("flow_counts", {})
    lab = digest.get("lab", {})
    per_flow = []
    for flow, name in FLOW_NAMES.items():
        state = flow_counts.get(f"flow{flow}", {})
        relevant = [line for line in digest.get("logs", []) if f"flow {flow} " in line]
        observation = relevant[-1] if relevant else f"No recorded activity for {name.lower()}."
        if flow in (6, 7) and state.get("status") == "not_triggered":
            observation = "Not triggered (no incident)."
        per_flow.append({
            "flow": flow,
            "name": name,
            "observation": observation[:260],
            "issues": [],
            "recommendation": "",
        })
    n = int(digest.get("created_cars", 0))
    counts = digest.get("counts", {})
    return {
        "batch_size": n,
        "summary": (f"{n} cars in this batch; {counts.get('shipped', 0)} shipped, "
                    f"{counts.get('rework', 0)} sent to rework. "
                    f"{labels.get('caught', 0)} injected issue(s) were caught."),
        "per_flow": per_flow,
        "top_issues": [],
        "recommendations": [],
        "risk": "high" if labels.get("missed", 0) else "low",
    }


def normalize_feedback(raw: Any, batch_size: int) -> BatchFeedback:
    feedback = raw if isinstance(raw, BatchFeedback) else BatchFeedback.model_validate(raw)
    by_flow = {item.flow: item for item in feedback.per_flow if 1 <= item.flow <= 7}
    feedback.per_flow = [
        by_flow.get(flow, FlowFeedback(flow=flow, name=name, observation=""))
        for flow, name in FLOW_NAMES.items()
    ]
    feedback.batch_size = batch_size
    risk = str(feedback.risk).lower()
    feedback.risk = risk if risk in {"low", "medium", "high"} else "medium"
    return feedback
