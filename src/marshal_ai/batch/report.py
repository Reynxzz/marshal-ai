"""Assemble batch metrics, label outcomes, and bounded feedback input."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from marshal_ai.batch.feedback import build_digest


def build_report(batch: dict, cars: list[dict], events: list[dict], result: dict,
                 lab: dict, feedback: dict, model: str = "gemini-2.5-flash") -> dict:
    counts = batch.get("counts", {})
    flow_counts = batch.get("flow_counts", {})
    shipped_data = result.get("shipped", {})
    shipped = sum(len(v) for v in shipped_data.values() if isinstance(v, list)) if isinstance(shipped_data, dict) else 0
    rework = sum(car.get("state") == "rework" for car in cars)
    n = int(batch.get("created_cars", 0))
    flow3 = flow_counts.get("flow3", {})
    flow4 = flow_counts.get("flow4", {})
    flow6 = flow_counts.get("flow6", {})
    flow7 = flow_counts.get("flow7", {})
    labels = result.get("labels", {})
    duration = None
    started, finished = batch.get("started_at"), batch.get("finished_at")
    if started and finished:
        try:
            duration = max(0.0, (datetime.fromisoformat(str(finished)) - datetime.fromisoformat(str(started))).total_seconds())
        except (TypeError, ValueError):
            duration = None
    digest = build_digest(batch, cars, events, result, lab)
    return {
        "batch_id": batch["batch_id"],
        "status": "done",
        "engine": batch.get("engine", "carla"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "batch_size": n,
        "counts": {
            "requested": batch.get("requested_cars", n),
            "created": n,
            "finished": batch.get("finished_cars", 0),
            "shipped": shipped,
            "rework": rework,
            "exceptions": digest["exceptions"],
        },
        "duration_s": duration,
        "flow_kpis": {
            "flow1": {"cars_per_hour_to_yard": round(3600 * int(flow_counts.get("flow1", {}).get("in_yard", 0)) / duration, 2) if duration else 0,
                      "line_to_yard_min_clean": None},
            "flow2": {key: flow_counts.get("flow2", {}).get(key, 0) for key in ("cases", "resolved", "escalated")},
            "flow3": {"first_pass_yield": round(flow3.get("pass", 0) / n, 3) if n else 0,
                      "review_share": round(flow3.get("review", 0) / n, 3) if n else 0,
                      "false_alarms": labels.get("false_alarms", 0)},
            "flow4": {"disagreements": flow4.get("disagreements", 0),
                      "failed_items": result.get("functional_failures", [])},
            "flow5": {"rework_rate": round(rework / n, 3) if n else 0,
                      "shipped_with_open_finding": 0},
            "flow6": {"status": flow6.get("status", "not_triggered"),
                      "scenario_id": lab.get("scenario_id")},
            "flow7": {"status": flow7.get("status", "not_triggered"),
                      "runs": flow7.get("runs", 0), "robust": lab.get("adversarial", {}).get("robust")},
        },
        "labels": labels,
        "gemini": feedback,
        "log": {"events": digest["log_events"], "exceptions": digest["exceptions"]},
    }
