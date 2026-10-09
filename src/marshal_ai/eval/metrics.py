"""Scores per flow, named after the design's KPIs where it has one.

Every flow also gets consistency (how often repeated runs of a case give the majority answer:
a model that is right only some of the time is not a gate) and latency against the design's time
budget. Rates are 0..1, or None when no case could produce them.
"""

from __future__ import annotations

from collections import Counter
from statistics import median

from marshal_ai.eval.dataset import AssistCase, GateCase, TrackCase

GATE_BUDGET_S = 5.0          # design, "A car's journey": under 5 s while the car rolls through


def _rate(hits: list[bool]) -> float | None:
    return round(sum(hits) / len(hits), 3) if hits else None


def _consistency(answers: dict[str, list]) -> float | None:
    shares = [Counter(map(str, a)).most_common(1)[0][1] / len(a) for a in answers.values() if a]
    return round(sum(shares) / len(shares), 3) if shares else None


def _latency(outcomes: list[dict]) -> dict:
    seconds = sorted(o["seconds"] for o in outcomes)
    if not seconds:
        return {}
    p95 = seconds[min(len(seconds) - 1, int(0.95 * len(seconds)))]
    return {"median_s": round(median(seconds), 2), "p95_s": p95, "max_s": seconds[-1]}


def gate_metrics(cases: list[GateCase], results: dict[str, list[dict]]) -> dict:
    clean = [c for c in cases if not c.expect_mismatch_items and "PASS" in [v.value for v in c.expect_verdicts]]
    variant = [c for c in cases if c.expect_mismatch_items]
    runs = [(c, o) for c in cases for o in results.get(c.id, [])]
    latency = _latency([o for _, o in runs])
    return {
        "accuracy": _rate([o["verdict"] in [v.value for v in c.expect_verdicts] for c, o in runs]),
        # first-pass yield: clean cars passed straight through, without a person
        "first_pass_yield": _rate([o["verdict"] == "PASS" for c in clean for o in results.get(c.id, [])]),
        "review_share": _rate([o["verdict"] == "REVIEW" for _, o in runs]),
        # false alarms: a finding or mismatch reported on a car that has neither
        "false_alarm_rate": _rate([bool(o["findings"] or o["mismatch_items"])
                                   for c in clean for o in results.get(c.id, [])]),
        # build-sheet mismatches the gate should catch, and did
        "mismatch_recall": _rate([item in o["mismatch_items"] for c in variant
                                  for o in results.get(c.id, []) for item in c.expect_mismatch_items]),
        "consistency": _consistency({c.id: [o["verdict"] for o in results.get(c.id, [])] for c in cases}),
        "latency": latency,
        "within_budget": _rate([o["seconds"] <= GATE_BUDGET_S for _, o in runs]),
    }


def track_metrics(cases: list[TrackCase], results: dict[str, list[dict]]) -> dict:
    runs = [(c, o) for c in cases for o in results.get(c.id, [])]
    return {
        "accuracy": _rate([o["camera_ok"] is c.expect_seen for c, o in runs]),
        # the failure the check exists to catch: a lamp that is out, confirmed as working
        "false_confirm_rate": _rate([o["camera_ok"] is True for c, o in runs if not c.expect_seen]),
        # a working lamp the camera contradicts: fails a healthy car
        "false_reject_rate": _rate([o["camera_ok"] is False for c, o in runs if c.expect_seen]),
        # too unsure to say either way: goes to a person
        "unconfirmed_rate": _rate([o["camera_ok"] is None for _, o in runs]),
        "consistency": _consistency({c.id: [o["camera_ok"] for o in results.get(c.id, [])] for c in cases}),
        "latency": _latency([o for _, o in runs]),
    }


def assist_metrics(cases: list[AssistCase], results: dict[str, list[dict]]) -> dict:
    runs = [(c, o) for c in cases for o in results.get(c.id, [])]
    return {
        "recommendation_accuracy": _rate([o["recommended"] == c.expect_recommended for c, o in runs]),
        "people_detection_accuracy": _rate([o["people_or_moving_equipment"] is c.expect_people
                                            for c, o in runs]),
        # design rule: a high-risk option is never recommended (target 0)
        "high_risk_recommended": sum(o["recommended_risk"] == "high" for _, o in runs),
        "consistency": _consistency({c.id: [o["recommended"] for o in results.get(c.id, [])]
                                     for c in cases}),
        "latency": _latency([o for _, o in runs]),
    }


METRICS = {"gate": gate_metrics, "track": track_metrics, "assist": assist_metrics}
