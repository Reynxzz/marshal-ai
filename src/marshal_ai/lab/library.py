"""Retrieval library for flow 6: validated specs shown to Gemini as examples for a new request.

ChatScene (design, flow 6 "The method Marshal keeps") translates a description into its scenario
language with a retrieval database of description-and-code pairs. Marshal's pairs are a request and
the spec that was written for it: a few hand-checked seeds, plus every scenario an engineer has put
in the test library. A new request is shown the pairs most like it, rather than the same example
every time.

Retrieval is lexical: words shared with the request, weighted by how rare they are across the
library (idf), after folding plant synonyms (person/pedestrian -> worker, evening -> dusk). The
library is tens of specs, the words that matter are the closed vocabulary of the spec (actor types,
zones, light, weather, states), and a ranking that can be read off the words is easy to check.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from marshal_ai.records import Scenario, ScenarioStatus

if TYPE_CHECKING:
    from marshal_ai.store import Store


@dataclass(frozen=True)
class Example:
    request: str
    spec: dict

    @property
    def id(self) -> str:
        return self.spec["id"]

    @property
    def from_incident(self) -> bool:
        return self.spec.get("source", "").startswith("assistance")


# Each seed is validated, judged fair and passes its dry run on the quick simulator
# (tests/test_flow6_7_lab.py::test_every_seed_example_is_a_valid_passing_spec).
SEEDS = [
    # the design's flow 6 example
    Example("A worker steps out from behind a parked forklift at dusk while a car turns into the finishing lane.", {
        "id": "SC-0142", "source": "supervisor request", "map_zone": "finishing_corner",
        "time_of_day": "dusk", "weather": "clear", "ego": {"route": "line_end->finishing", "speed_kmh": 8},
        "actors": [
            {"type": "forklift", "state": "parked", "position": "finishing_corner.p3"},
            {"type": "worker", "start": "behind:forklift", "walk_speed_ms": 1.3,
             "trigger": {"ego_distance_m": 9}},
        ],
        "pass_criteria": ["no_contact", "min_gap_to_person_m >= 1.5", "speed_near_person_kmh <= 5"],
    }),
    # a case from the assistance desk (flow 2): without it Gemini put the box where the car starts
    # on all three tries at scene 3's incident
    Example("A box in the lane at the test track with a worker beside it; the car stopped and waited.", {
        "id": "SC-0150", "source": "assistance case AC-VIN-123-1",
        "description": "A box in the lane at the test track with a worker beside it; the car stopped and waited. "
                       "The box blocks the lane, so reached_goal is not required.",
        "map_zone": "test_track", "time_of_day": "noon", "weather": "clear",
        "ego": {"route": "inspection_gate->test_track->report_point", "speed_kmh": 8},
        "actors": [
            {"type": "box", "state": "stationary", "position": "test_track"},
            {"type": "worker", "state": "stationary", "position": "test_track"},
        ],
        "pass_criteria": ["no_contact", "min_gap_to_person_m >= 1.5", "speed_near_person_kmh <= 5"],
    }),
    Example("The car in front brakes suddenly on the test track.", {
        "id": "SC-0160", "source": "supervisor request",
        "description": "A car ahead brakes to a stop in the lane; it stays there, so reached_goal is not required.",
        "map_zone": "test_track",
        "time_of_day": "noon", "weather": "clear",
        "ego": {"route": "inspection_gate->test_track->report_point", "speed_kmh": 8},
        "actors": [{"type": "car", "state": "braking", "position": "test_track", "speed_kmh": 8,
                    "trigger": {"ego_distance_m": 10}}],
        "pass_criteria": ["no_contact", "min_gap_to_vehicle_m >= 1.0"],
    }),
    Example("A forklift crosses the lane in front of the car at the finishing corner.", {
        "id": "SC-0170", "source": "supervisor request", "map_zone": "finishing_corner",
        "time_of_day": "noon", "weather": "clear", "ego": {"route": "report_point->finishing", "speed_kmh": 8},
        "actors": [{"type": "forklift", "state": "crossing", "position": "finishing_corner.p2", "speed_kmh": 5,
                    "trigger": {"ego_distance_m": 12}}],
        "pass_criteria": ["no_contact", "min_gap_to_vehicle_m >= 1.0", "reached_goal"],
    }),
    Example("A worker walks across the test track at night in the rain.", {
        "id": "SC-0180", "source": "supervisor request", "map_zone": "test_track",
        "time_of_day": "night", "weather": "rain",
        "ego": {"route": "inspection_gate->test_track->report_point", "speed_kmh": 8},
        "actors": [{"type": "worker", "state": "crossing", "position": "test_track", "walk_speed_ms": 1.2,
                    "trigger": {"ego_distance_m": 12}}],
        "pass_criteria": ["no_contact", "min_gap_to_person_m >= 1.5", "speed_near_person_kmh <= 5",
                          "reached_goal"],
    }),
]

# Plant words folded onto the spec's own vocabulary before matching.
SYNONYMS = {
    "person": "worker", "people": "worker", "pedestrian": "worker", "operator": "worker",
    "employee": "worker", "man": "worker", "woman": "worker", "staff": "worker",
    "evening": "dusk", "sunset": "dusk", "twilight": "dusk", "dark": "night", "midnight": "night",
    "day": "noon", "daytime": "noon", "afternoon": "noon", "rainy": "rain", "wet": "rain", "foggy": "fog",
    "pallet": "pallet_stack", "pallets": "pallet_stack", "stack": "pallet_stack",
    "carton": "box", "debris": "box", "object": "box", "obstacle": "box", "crate": "box", "parcel": "box",
    "vehicle": "car", "brake": "braking", "brakes": "braking", "stops": "braking", "stop": "braking",
    "cross": "crossing", "crosses": "crossing", "across": "crossing", "walks": "crossing",
    "corner": "finishing_corner", "bend": "finishing_corner", "track": "test_track",
    "behind": "behind", "hidden": "behind", "emerges": "behind", "steps": "behind",
}
STOPWORDS = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "into", "from", "with", "while",
             "is", "it", "its", "car", "lane", "then", "that", "this", "by", "for", "out", "front",
             "test", "suddenly"}


def terms(text: str) -> list[str]:
    words = re.findall(r"[a-z_]+", text.lower())
    out = []
    for w in words:
        w = SYNONYMS.get(w, w)
        if w not in STOPWORDS and len(w) > 2:
            out.append(w)
    return out


def _example_terms(e: Example) -> Counter:
    """The request's words and the spec's facts, so a library entry with a short request still matches."""
    s = e.spec
    facts = [s.get("map_zone", ""), s.get("time_of_day", ""), s.get("weather", "")]
    for a in s.get("actors", []):
        facts += [a.get("type", ""), a.get("state", "")]
        if str(a.get("start", "")).startswith("behind:"):
            facts.append("behind")
    return Counter(terms(e.request) + [f for f in facts if f and f not in ("clear", "stationary")])


def library_examples(store: "Store") -> list[Example]:
    """Seeds plus the reviewed scenarios in the library. Regression variants (SC-0142-v3) are left
    out: they repeat their base scenario with one value changed."""
    found = {e.id: e for e in SEEDS}
    for s in store.list(Scenario):
        if s.status != ScenarioStatus.IN_LIBRARY or "-v" in s.id or "regression" in s.tags:
            continue
        request = s.notes.split(" | ")[0].strip() or s.spec.get("description", "")
        if request:
            found[s.id] = Example(request, s.spec)
    return list(found.values())


def retrieve(request: str, source: str, examples: list[Example], k: int = 2) -> list[Example]:
    """The k examples most like the request, best first. A request from the assistance desk always
    gets the closest incident example among them, as an incident's form differs from a supervisor's."""
    if not examples:
        return []
    docs = [(e, _example_terms(e)) for e in examples]
    df = Counter(t for _, d in docs for t in d)
    idf = {t: math.log(1 + len(docs) / n) for t, n in df.items()}
    query = set(terms(request))

    def score(d: Counter) -> float:
        return sum(idf[t] for t in query & d.keys()) / math.sqrt(len(d) or 1)

    ranked = sorted(docs, key=lambda ed: (-score(ed[1]), ed[0].id))
    chosen = [e for e, _ in ranked[:k]]
    if source.startswith("assistance") and not any(e.from_incident for e in chosen):
        incident = next((e for e, _ in ranked if e.from_incident), None)
        if incident is not None:
            chosen = chosen[: k - 1] + [incident]
    return chosen
