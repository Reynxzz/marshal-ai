"""Deterministic stand-ins for every Gemini task (MARSHAL_LLM=offline).

For rehearsals and tests without credentials. Perception tasks read the fake world's
ground truth (Media.truth); reasoning tasks use simple heuristics. They are not meant
to be clever, only predictable. With real Gemini these tasks go to the model.
"""

from __future__ import annotations

import json
import re

from marshal_ai.driving.routines import R12
from marshal_ai.llm import FakeLLM, LLMCall

STEP_LAMPS = {s.name: set(s.lamps) for s in R12}
DEFECT_CONFIDENCE = {"scratch": 0.78}          # small scratches go to human review, as in the design


def _section(prompt: str, start: str, end: str | None = None):
    pattern = re.escape(start) + (r"(.*?)" + re.escape(end) if end else r"(.*)")
    m = re.search(pattern, prompt, re.S)
    if not m:
        return None
    text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# ---------- perception ----------

def visual_defects(call: LLMCall) -> dict:
    truth = call.media[0].truth or {}
    findings = [{"zone": d["zone"], "type": d["type"], "size_cm": d.get("size_cm"),
                 "confidence": DEFECT_CONFIDENCE.get(d["type"], 0.94)} for d in truth.get("defects", [])]
    return {"findings": findings, "clear_confidence": 0.96}


def visual_variant(call: LLMCall) -> dict:
    sheet = json.loads(re.search(r"Build sheet: (\{.*\})", call.prompt).group(1))
    truth = call.media[0].truth or {}
    mismatches = []
    for item in ("colour", "model", "trim"):
        if truth.get(item) and truth[item] != sheet.get(item):
            mismatches.append({"item": item, "expected": sheet[item], "observed": truth[item], "confidence": 0.96})
    for option in sheet.get("options", []):
        if option not in truth.get("fitted", []):
            mismatches.append({"item": option, "expected": "fitted", "observed": "not fitted", "confidence": 0.93})
    return {"observed_colour": truth.get("colour", "unknown"), "mismatches": mismatches, "confidence": 0.97}


def track_step(call: LLMCall) -> dict:
    truth = call.media[0].truth or {}
    step = truth.get("step")
    dead = STEP_LAMPS.get(step, set()) & set(truth.get("dead_lamps", []))
    if dead:
        return {"seen": False, "confidence": 0.93,
                "observation": f"{', '.join(sorted(dead))} not lit; other lamps lit"}
    return {"seen": True, "confidence": 0.95, "observation": f"{step} as expected"}


def assist_assess(call: LLMCall) -> dict:
    scene = (call.media[0].truth if call.media else None) or {}
    options = _section(call.prompt, "Offered options: ", "\nCar state") or []
    if isinstance(options, str):
        options = re.findall(r"'([^']+)'", options)
    people = bool(scene.get("people"))
    ranking = []
    for opt in options:
        if opt.startswith("wait"):
            ranking.append({"option": opt, "reason": "clears once the object is removed; no movement near people",
                            "risk": "low"})
        elif opt.startswith("pass"):
            ranking.append({"option": opt, "reason": "moves past the object" +
                            (" with a person nearby" if people else " in an empty lane"),
                            "risk": "high" if people else "low"})
        else:
            ranking.append({"option": opt, "reason": "avoids the area at the cost of time", "risk": "medium"})
    order = {"low": 0, "medium": 1, "high": 2}
    ranking.sort(key=lambda r: (order[r["risk"]], 0 if r["option"].startswith("wait") == people else 1))
    obj = scene.get("object", "object")
    where = scene.get("where", "in lane")
    dist = scene.get("distance_m")
    situation = f"{obj.capitalize()} {where}" + (f", car stopped {dist} m before it" if dist else "")
    if people:
        situation += f"; worker {scene['people'][0].get('distance_m', '?')} m away"
    return {"situation": situation + ".", "people_or_moving_equipment": people, "ranking": ranking,
            "recommended": ranking[0]["option"] if ranking else None, "confidence": 0.86}


# ---------- reasoning ----------

def yard_exception(call: LLMCall) -> dict:
    kind = _section(call.prompt, "Exception: ", "\nDetails")
    details = _section(call.prompt, "Details: ", "\nPlant state")
    state = _section(call.prompt, "Plant state: ", "\nReturn a plan")
    actions: list[dict] = []
    interval = None
    if kind == "truck_delayed":
        trucks = [t["truck_id"] for t in state["trucks_by_eta"]]
        nearest = state["yard_slots_nearest_loading_bay_first"]
        free = list(state["free_slots"])
        ranked = {t: i for i, t in enumerate(trucks)}
        yard = [c for c in state["cars"] if c["state"] == "yard"]
        yard.sort(key=lambda c: ranked.get(c["truck_id"], 99))
        # the new next truck's cars move into the free slots nearest the loading bay
        next_truck = trucks[0] if trucks else None
        for car in yard:
            if car["truck_id"] != next_truck or car["do_not_ship"]:
                continue
            better = [s for s in free if nearest.index(s) < nearest.index(car["yard_slot"])]
            if better:
                slot = better[0]
                free.remove(slot)
                free.append(car["yard_slot"])
                actions.append({"vin": car["vin"], "action": "go", "waypoints": [slot],
                                "reason": f"{next_truck} now loads first; move nearer the loading bay"})
        summary = (f"Truck {details['truck_id']} is {details['delay_min']} min late; "
                   f"re-slot {len(actions)} car(s) for {next_truck} nearer the loading bay")
        reasoning = "Loading the truck that arrives first without shuffling cars saves more time than holding slots."
    elif kind == "track_closed":
        waiting = details["cars_waiting"]
        if len(waiting) <= 3:
            actions = [{"vin": v, "action": "hold", "reason": "test track closed"} for v in waiting]
            summary = f"Hold {len(waiting)} car(s) at line end until the test track reopens"
        else:
            actions = [{"vin": v, "action": "go", "waypoints": ["buffer_zone_b"], "flag": "untested",
                        "reason": "line end full while the track is closed"} for v in waiting]
            summary = f"Park {len(waiting)} untested cars in buffer zone B with a flag"
        reasoning = "Cars cannot ship untested; holding keeps them close while the line end has room."
    elif kind == "rework_bay_full":
        free = state["free_slots"]
        slot = free[-1] if free else "buffer_zone_b"
        actions = [{"vin": details["vin"], "action": "go", "waypoints": [slot], "do_not_ship": True,
                    "flag": "rework_waiting", "reason": f"{details['bay']} is full"}]
        summary = f"Send {details['vin']} to {slot} with a do-not-ship hold until {details['bay']} frees up"
        reasoning = "Blocking the report point would stop every car behind it; a held car in the yard blocks nothing."
    elif kind == "line_outpacing_yard":
        interval = round(3600 / max(details["yard_rate_per_h"], 1), 1)
        summary = f"Slow releases to one every {interval:.0f} s to match the yard"
        reasoning = (f"The line makes {details['line_rate_per_h']:.0f}/h but the yard absorbs "
                     f"{details['yard_rate_per_h']:.0f}/h; releasing faster only moves the queue.")
    else:
        summary, reasoning = f"No action for {kind}", "Nothing safe to change automatically."
    return {"summary": summary, "reasoning": reasoning, "actions": actions, "release_interval_s": interval}


def report_write(call: LLMCall) -> dict:
    visual = _section(call.prompt, "Visual result: ", "\nFunctional result")
    functional = _section(call.prompt, "Functional result: ", "\nRework bays")
    parts = []
    if isinstance(visual, dict):
        for f in visual.get("findings", []):
            size = f" {f['size_cm']:g} cm" if f.get("size_cm") else ""
            parts.append(f"{f['type'].replace('_', ' ')}{size} on the {f['zone'].replace('_', ' ')}")
        for m in visual.get("build_sheet_mismatches", []):
            parts.append(m.split(" (")[0])
    if isinstance(functional, dict):
        for i in functional.get("checklist", []):
            if not (i.get("telemetry_ok") and i.get("camera_ok")):
                parts.append(f"{i['name'].replace('_', ' ')} failed on camera while telemetry reported "
                             f"{'OK' if i.get('telemetry_ok') else 'a fault'}")
    summary = ("Findings: " + "; ".join(parts) + ".") if parts else "No findings."
    return {"summary": summary + " Safety items are repaired first; only failed items are re-inspected.",
            "steps": []}


# ---------- test lab ----------

def scenario_spec(call: LLMCall) -> dict:
    text = (_section(call.prompt, "Situation: ", "\n\nPlant map") or "").lower()
    tod = "night" if "night" in text else "dusk" if ("dusk" in text or "evening" in text) else "noon"
    weather = "rain" if "rain" in text else "fog" if "fog" in text else "clear"
    occluder = next((o for k, o in [("forklift", "forklift"), ("pallet", "pallet_stack"),
                                    ("parked car", "parked_car"), ("box", "pallet_stack")] if k in text), None)
    zone_point = "finishing_corner.p3"
    if "line end" in text or "line_end" in text:
        zone_point = "inspection_gate"
    elif "yard" in text:
        zone_point = "finishing"
    zone = {"finishing_corner.p3": "finishing_corner", "inspection_gate": "inspection_gate",
            "finishing": "finishing"}[zone_point]
    route_end = "finishing" if zone_point != "finishing" else "yard.entry"
    actors = []
    person = any(w in text for w in ("worker", "person", "pedestrian", "people"))
    if occluder:
        actors.append({"type": occluder, "state": "parked", "position": zone_point})
    if person:
        worker = {"type": "worker", "walk_speed_ms": 1.3, "trigger": {"ego_distance_m": 9}}
        worker["start"] = f"behind:{occluder}" if occluder and ("behind" in text or "steps out" in text) else zone_point
        actors.append(worker)
    criteria = ["no_contact"]
    if person:
        criteria += ["min_gap_to_person_m >= 1.5", "speed_near_person_kmh <= 5"]
    return {"map_zone": zone, "time_of_day": tod, "weather": weather,
            "description": text[:200],
            "ego": {"route": f"line_end->{route_end}", "speed_kmh": 8}, "actors": actors,
            "pass_criteria": criteria}


# Harder first: the order a cautious engineer would try, from single changes to combinations.
ADVERSARIAL_LADDER = [
    {"light": "night"}, {"occluder": "pallet_stack"}, {"trigger_distance_m": 7.0},
    {"worker_speed_ms": 2.1}, {"ego_speed_kmh": 10.0}, {"weather": "fog"},
    {"light": "night", "occluder": "pallet_stack"}, {"light": "night", "trigger_distance_m": 7.0},
    {"light": "night", "occluder": "pallet_stack", "trigger_distance_m": 7.0},
    {"light": "night", "occluder": "pallet_stack", "trigger_distance_m": 7.0, "worker_speed_ms": 2.1},
    {"trigger_distance_m": 5.0, "worker_speed_ms": 2.5}, {"second_actor": "car_ahead_braking"},
]


def adversarial_propose(call: LLMCall) -> dict:
    runs = _section(call.prompt, "Runs so far: ", "\nPropose") or []
    n = int(re.search(r"Propose (\d+)", call.prompt).group(1))
    seen = [r["changes"] for r in runs] if isinstance(runs, list) else []
    batch = [c for c in ADVERSARIAL_LADDER if c not in seen][:n]
    closest = min((r for r in runs if r.get("margins")), key=lambda r: min(r["margins"].values()),
                  default=None) if isinstance(runs, list) else None
    reasoning = ("Start with single changes that make detection harder, then combine them."
                 if closest is None else
                 f"Closest to failing so far: {closest['changes']} (margins {closest['margins']}); "
                 "combine it with other changes that shorten detection time.")
    return {"reasoning": reasoning, "variations": batch}


def adversarial_report(call: LLMCall) -> dict:
    changes = _section(call.prompt, "Changes: ", "\nMetrics") or {}
    if isinstance(changes, str):
        changes = json.loads(changes.replace("'", '"'))
    metrics = _section(call.prompt, "Metrics: ", "\nFailed criteria") or {}
    if isinstance(metrics, str):
        metrics = json.loads(metrics.replace("'", '"'))
    failed = _section(call.prompt, "Failed criteria: ")
    causes = []
    if changes.get("light") in ("night", "dusk"):
        causes.append("late detection in low light")
    if changes.get("occluder") in ("pallet_stack", "forklift"):
        causes.append("the worker is hidden by a tall occluder until close")
    if "trigger_distance_m" in changes or "worker_speed_ms" in changes:
        causes.append("the worker enters the lane with little time to react")
    if changes.get("ego_speed_kmh", 0) >= 9:
        causes.append("higher approach speed lengthens the stopping distance")
    cause = "; ".join(causes) or "reaction time too long for the situation"
    fix = "reduce speed near finishing_corner to 5 km/h in low light" if changes.get("light") in ("night", "dusk") \
        else "reduce approach speed near occluded crossings to 5 km/h"
    gap = metrics.get("min_gap_to_person_m")
    return {"summary": f"With {changes}, the car came within {gap} m of the worker ({failed}).",
            "likely_cause": cause, "suggested_fix": fix}


def offline_llm() -> FakeLLM:
    return FakeLLM({
        "visual.defects": visual_defects,
        "visual.variant": visual_variant,
        "track.step": track_step,
        "assist.assess": assist_assess,
        "yard.exception": yard_exception,
        "report.write": report_write,
        "scenario.spec": scenario_spec,
        "adversarial.propose": adversarial_propose,
        "adversarial.report": adversarial_report,
    })
