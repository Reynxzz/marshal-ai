"""Scores per flow, named after the design's KPIs where it has one.

Every flow also gets consistency (how often repeated runs of a case give the majority answer:
a model that is right only some of the time is not a gate) and latency against the design's time
budget. Rates are 0..1, or None when no case could produce them.
"""

from __future__ import annotations

from collections import Counter
from statistics import median

from marshal_ai.eval.dataset import (AssistCase, DamageCase, GateCase, ReportCase, ScenarioCase, SearchCase,
                                     TrackCase)

GATE_BUDGET_S = 5.0          # design, "A car's journey": under 5 s while the car rolls through
CARD_BUDGET_S = 5.0          # design, flow 2: the card reaches the supervisor in about 5 s


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


def iou(a: list[int] | None, b: list[int] | None) -> float:
    """Overlap of two [ymin, xmin, ymax, xmax] boxes: 1 the same box, 0 apart."""
    if not a or not b:
        return 0.0
    h = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    w = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    inter = h * w
    return inter / (area(a) + area(b) - inter) if inter else 0.0


BOX_HIT_IOU = 0.5            # the usual bar for a detection to count as on the object


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


def _box_scores(ious: list[float]) -> dict:
    if not ious:
        return {"box_iou": None, "box_hit_rate": None}
    return {"box_iou": round(median(ious), 3), "box_hit_rate": _rate([v >= BOX_HIT_IOU for v in ious])}


def assist_metrics(cases: list[AssistCase], results: dict[str, list[dict]]) -> dict:
    runs = [(c, o) for c in cases for o in results.get(c.id, [])]
    return {
        "recommendation_accuracy": _rate([o["recommended"] == c.expect_recommended for c, o in runs]),
        "people_detection_accuracy": _rate([o["people_or_moving_equipment"] is c.expect_people
                                            for c, o in runs]),
        # design rule: a high-risk option is never recommended (target 0)
        "high_risk_recommended": sum(o["recommended_risk"] == "high" for _, o in runs),
        # the card boxes what is in the scene: each expected kind located with a usable box
        "object_recall": _rate([any(x["kind"] == e.kind and x["box_2d"] for x in o["objects"])
                                for c, o in runs for e in c.expect_objects]),
        # and boxes it where it is: the best overlap with the hand-labelled box (frame 0)
        **_box_scores([max([iou(x["box_2d"], e.box_2d) for x in o["objects"]
                            if x["kind"] == e.kind and x["frame"] == 0] or [0.0])
                       for c, o in runs for e in c.expect_objects if e.box_2d]),
        "consistency": _consistency({c.id: [o["recommended"] for o in results.get(c.id, [])]
                                     for c in cases}),
        "latency": _latency([o for _, o in runs]),
        "within_budget": _rate([o["seconds"] <= CARD_BUDGET_S for _, o in runs]),
    }


def damage_metrics(cases: list[DamageCase], results: dict[str, list[dict]]) -> dict:
    damaged = [(c, o) for c in cases if c.expect_damaged for o in results.get(c.id, [])]
    clean = [(c, o) for c in cases if not c.expect_damaged for o in results.get(c.id, [])]
    flagged = lambda o: bool(o["findings"])           # a defect reported at or over the review bar
    found = [(c, o) for c, o in damaged if flagged(o)]
    per_kind = {}
    for kind in sorted({k for c in cases for k in c.expect_kinds}):
        runs = [(c, o) for c, o in damaged if kind in c.expect_kinds]
        per_kind[kind] = _rate([any(kind in f["kinds"] for f in o["findings"]) for _, o in runs])
    return {
        # confirmed defects: damage the gate reported (it would reach a person or rework)
        "damage_recall": _rate([flagged(o) for _, o in damaged]),
        "damage_failed": _rate([o["verdict"] == "FAIL" for _, o in damaged]),
        # of the damage found, reported as a kind that is really there
        "kind_precision": _rate([any(set(f["kinds"]) & set(c.expect_kinds) for f in o["findings"])
                                 for c, o in found]),
        # of the damage found on a part inside a gate zone, placed in that zone (doors either side)
        "zone_accuracy": _rate([any(f["zone"] in c.expect_zones for f in o["findings"])
                                for c, o in found if c.expect_zones]),
        "recall_by_kind": per_kind,
        # false alarms: a defect reported on a clean panel
        "false_alarm_rate": _rate([flagged(o) for _, o in clean]),
        # clean panels the gate passed straight through
        "clean_pass_rate": _rate([o["verdict"] == "PASS" for _, o in clean]),
        "consistency": _consistency({c.id: [o["verdict"] for o in results.get(c.id, [])] for c in cases}),
        "latency": _latency([o for _, o in damaged + clean]),
    }


SAFETY_BAYS = {"mechanical", "electrical"}
# words a summary uses for a safety defect, and for everything else, to see which it leads with
SAFETY_WORDS = ("brake", "steering", "light", "lamp", "indicator", "blinker")
OTHER_WORDS = ("scratch", "scuff", "dent", "paint", "chip", "gap", "badge", "colour", "color", "variant")


def _first(text: str, words: tuple[str, ...]) -> int | None:
    hits = [i for w in words if (i := text.find(w)) >= 0]
    return min(hits) if hits else None


def leads_with_safety(summary: str) -> bool | None:
    """Whether the summary names the safety defect before the cosmetic one; None if it lacks either."""
    text = summary.lower()
    safety, other = _first(text, SAFETY_WORDS), _first(text, OTHER_WORDS)
    return None if safety is None or other is None else safety < other


def report_metrics(cases: list[ReportCase], results: dict[str, list[dict]]) -> dict:
    runs = [(c, o) for c in cases for o in results.get(c.id, [])]
    mixed = [(c, o) for c, o in runs if SAFETY_BAYS & set(c.expect_bays) and set(c.expect_bays) - SAFETY_BAYS]
    drafted = [(c, o) for c, o in runs if o["draft_bays"] is not None]
    # the design's rule is safety items first; the order among cosmetic bays is the router's own
    in_order = lambda bays: bays == sorted(bays, key=lambda b: b not in SAFETY_BAYS)
    return {
        # what reaches the supervisor: Gemini's plan after the router checks it against the bay table
        "bays_right": _rate([o["final_bays"] == c.expect_bays for c, o in runs]),
        # Gemini's own plan, before that check: the bays the table gives, and safety first
        "gemini_bays_right": _rate([set(o["draft_bays"]) == set(c.expect_bays) for c, o in drafted]),
        "gemini_safety_first": _rate([in_order(o["draft_bays"]) for c, o in drafted]),
        # with a safety and a cosmetic defect, the summary leads with the safety one
        "summary_safety_first": _rate([v for _, o in mixed if (v := leads_with_safety(o["summary"])) is not None]),
        "summary_names_both": _rate([leads_with_safety(o["summary"]) is not None for _, o in mixed]),
        "consistency": _consistency({c.id: [o["final_bays"] for o in results.get(c.id, [])] for c in cases}),
        "latency": _latency([o for _, o in runs]),
    }


def faithful(case: ScenarioCase, spec: dict) -> dict[str, bool]:
    """Each thing the request asked for, and whether the test has it."""
    actors = spec.get("actors") or []
    types = [a.get("type") for a in actors]
    criteria = " ".join(spec.get("pass_criteria") or [])
    checks = {
        "zone": spec.get("map_zone") == case.expect_zone,
        "actors": all(t in types for t in case.expect_actors),
        "placed": all(any(a.get("type") == t and point in (a.get("position"), a.get("start")) for a in actors)
                      for t, point in case.expect_at.items()),
        "criteria": all(name in criteria for name in case.expect_metrics),
    }
    if case.expect_time_of_day:
        checks["time_of_day"] = spec.get("time_of_day") == case.expect_time_of_day
    return checks


def scenario_metrics(cases: list[ScenarioCase], results: dict[str, list[dict]]) -> dict:
    runs = [(c, o) for c in cases for o in results.get(c.id, [])]
    made = [(c, o) for c, o in runs if o["status"] == "dry_run_ok"]
    checks = [faithful(c, o["spec"]) for c, o in made]
    return {
        # a runnable test came out of the fix loop (3 fixes), without an engineer
        "generated": _rate([o["status"] == "dry_run_ok" for _, o in runs]),
        "first_try": _rate([o["status"] == "dry_run_ok" and o["attempts"] == 1 for _, o in runs]),
        "attempts_median": median([o["attempts"] for _, o in runs]) if runs else None,
        # of the tests made, the ones that test what was asked: every check below holds
        "faithful": _rate([all(c.values()) for c in checks]),
        "faithful_by_check": {k: _rate([c[k] for c in checks if k in c]) for k in sorted({k for c in checks for k in c})},
        "consistency": _consistency({c.id: [o["status"] for o in results.get(c.id, [])] for c in cases}),
        "latency": _latency([o for _, o in runs]),
    }


def _search_scores(searches: list[dict]) -> dict:
    found = [s for s in searches if s["found"]]
    return {
        "found": _rate([s["found"] for s in searches]),
        # the design's "time to first failure", in runs: lower is a better search
        "runs_to_failure_median": median([s["runs_to_failure"] for s in found]) if found else None,
        # the design's "failures found per 100 runs": a search stops at its first failure
        "failures_per_100_runs": round(100 * len(found) / sum(s["runs"] for s in searches), 1) if searches else None,
        # changes from the passing case left after shrinking: fewer is easier to explain
        "minimal_changes_median": median([len(s["minimal"]) for s in found]) if found else None,
        # proposals a careful driver fails too, skipped: wasted proposals
        "unavoidable_per_search": round(sum(s["unavoidable"] for s in searches) / len(searches), 1) if searches else None,
    }


def search_metrics(cases: list[SearchCase], results: dict[str, list[dict]]) -> dict:
    runs = [o for c in cases for o in results.get(c.id, [])]
    model = _search_scores(runs)
    baseline = _search_scores([b for o in runs for b in o["random"]])
    per_case = {}
    for c in cases:
        outs = results.get(c.id, [])
        g = _search_scores(outs)["runs_to_failure_median"]
        b = _search_scores([b for o in outs for b in o["random"]])["runs_to_failure_median"]
        per_case[c.id] = f"model {g} vs random {b}"
    return {
        "model": model,
        "random": baseline,
        "runs_to_failure_by_case": per_case,
        "explained": _rate([o["explanation"] is not None for o in runs if o["found"]]),
        "consistency": _consistency({c.id: [str(o["minimal"]) for o in results.get(c.id, [])] for c in cases}),
        "latency": _latency(runs),
    }


METRICS = {"gate": gate_metrics, "track": track_metrics, "assist": assist_metrics,
           "damage": damage_metrics, "report": report_metrics, "scenario": scenario_metrics,
           "search": search_metrics}
