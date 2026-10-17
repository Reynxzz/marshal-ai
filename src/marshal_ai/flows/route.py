"""Route choice: Gemini picks the way a car drives across the plant, as a decision model.

The driving system used to take the shortest way to wherever it was sent. From the report point to
the yard there are two: through the finishing corner, a blind corner the forklifts use, or along
lane C, 13 m longer. Which is better depends on what is going on: a forklift working in the corner,
a car stopped on one of them, people crossing. That is a judgement, and the plant's other judgements
are Gemini's, so this one is too.

How it decides:
  1. The plant map lists the ways from here to there (PlantMap.routes).
  2. The rules strike any way through a closed zone. One way left: it is taken, no question asked.
  3. With nothing going on along any of them, the shortest is taken; no call (MARSHAL_ROUTE=always
     asks every time, for the demo; "never" keeps the shortest whatever happens).
  4. Otherwise Gemini is asked which, as a decision: one token, a probability for each way, from
     what each way passes - its length and time, cars on it, a car stopped on it, hazards reported
     in its zones. A failed call takes the shortest way, as before.

Measured (python -m marshal_ai.eval.routes --runs 3, gemini-2.5-flash, asia-southeast1, 2026-10-15):
27 of 30, median 0.81 s. The same answer every run. Its one disagreement with the expected way: a
forklift "parked and switched off, 10 m from the road" in the corner, which it still avoided (0.99)
- cautious rather than wrong. A keyword stand-in (the offline handler) got 8 of 10: it avoids
finished paintwork and a parked forklift alike, because it cannot read what the hazard says.

The choice is the mission's waypoints, every point of the way, so the driving system drives that way
and no other. It is recorded on the car (Car.route, Car.route_by) and in YardTraffic.route_log.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from marshal_ai.flows.base import ask_or
from marshal_ai.llm import decision_schema
from marshal_ai.records import AssistanceCase, Car, CarState

if TYPE_CHECKING:
    from marshal_ai.runtime import Runtime

log = logging.getLogger("marshal")

TASK = "route.choose"

SYSTEM = (
    "You choose the way a finished car drives across a car plant, at walking pace, between named "
    "points. Each way is listed with what is on it right now. People and moving equipment such as "
    "forklifts matter most: send the car where it will not meet them, even if that way is longer. A "
    "car stopped on a way blocks it. Moving cars on a way only slow the car down a little. A longer "
    "way costs a minute or so; that is cheap next to a near miss. If nothing on the shorter way puts "
    "anyone at risk or blocks it, take the shorter way. Read what each hazard actually says: work "
    "that is finished, or something away from the road, is not a hazard to the car."
)


@dataclass
class Way:
    points: list[str]
    length_m: float
    zones: list[str]
    label: str = ""
    cars: list[str] = field(default_factory=list)        # other cars driving along it
    stopped: list[str] = field(default_factory=list)     # cars stopped on it waiting for help
    hazards: list[str] = field(default_factory=list)     # "zone: what"

    @property
    def quiet(self) -> bool:
        return not (self.cars or self.stopped or self.hazards)

    def minutes(self, speed_kmh: float) -> float:
        return self.length_m / (speed_kmh * 1000 / 60)

    def describe(self, speed_kmh: float) -> str:
        parts = [f"{self.length_m:.0f} m, about {self.minutes(speed_kmh):.1f} min",
                 "through " + ", ".join(self.zones)]
        parts.append("hazards: " + ("; ".join(self.hazards) if self.hazards else "none reported"))
        parts.append("stopped cars: " + (", ".join(self.stopped) if self.stopped else "none"))
        parts.append("cars driving on it: " + (", ".join(self.cars) if self.cars else "none"))
        return "; ".join(parts)


def _labels(ways: list[Way]) -> None:
    """Name each way by the first point only it passes: "via lane_c"."""
    for w in ways:
        others = [set(o.points) for o in ways if o is not w]
        own = next((p for p in w.points[1:-1] if all(p not in o for o in others)), None)
        w.label = f"via {own.split('.')[0]}" if own else f"way {ways.index(w) + 1}"
    seen: dict[str, int] = {}
    for w in ways:
        seen[w.label] = seen.get(w.label, 0) + 1
        if seen[w.label] > 1:
            w.label = f"{w.label} ({seen[w.label]})"


def ways(rt: "Runtime", vin: str, start: str, goal: str, in_use: dict[str, list[str]]) -> list[Way]:
    """The ways from start to goal the rules allow, with what is on each right now."""
    m = rt.map
    try:
        found = m.routes(start, goal)
    except (KeyError, ValueError):
        return []
    closed = rt.plant.closed_zones
    open_cases = [c for c in rt.store.list(AssistanceCase) if c.resolved_at is None]
    cars = {c.vin: c for c in rt.store.list(Car)}
    out = []
    for pts in found:
        zones = list(dict.fromkeys(m.points[p].zone for p in pts[1:]))
        if any(z in closed for z in zones):
            continue
        inner = set(pts[1:])
        w = Way(pts, m.route_length(pts), zones)
        w.stopped = sorted(f"{c.vin} at {c.location}" for c in open_cases if c.vin != vin and c.location in inner)
        stopped_vins = {s.split()[0] for s in w.stopped}
        w.cars = sorted(other for other, route in in_use.items()
                        if other != vin and other not in stopped_vins and inner & set(route[1:])
                        and (cars.get(other) is None or cars[other].state != CarState.SHIPPED))
        # a hazard is reported for a zone, or for one point on the map (lane_c is in the finishing
        # zone, which both ways to the yard pass, so "people crossing lane C" names the point)
        w.hazards = ([f"{z}: {rt.plant.hazards[z]}" for z in zones if rt.plant.hazards.get(z)]
                     + [f"{p}: {rt.plant.hazards[p]}" for p in pts[1:] if rt.plant.hazards.get(p)])
        out.append(w)
    _labels(out)
    return out


def prompt(vin: str, start: str, goal: str, options: list[Way], speed_kmh: float) -> str:
    lines = [f"Car {vin} drives from {start} to {goal}. The ways it can take:"]
    for w in options:
        lines.append(f"- {w.label}: {w.describe(speed_kmh)}")
    lines.append("")
    lines.append("Which way should it take?")
    return "\n".join(lines)


async def choose(rt: "Runtime", vin: str, start: str, goal: str, in_use: dict[str, list[str]],
                 speed_kmh: float, ask_when: str = "reason") -> tuple[list[str], dict]:
    """The waypoints to send, and a record of how they were chosen."""
    options = ways(rt, vin, start, goal, in_use)
    record = {"vin": vin, "from": start, "to": goal, "at": rt.clock().isoformat(),
              "ways": [{"label": w.label, "length_m": round(w.length_m), "hazards": w.hazards,
                        "stopped": w.stopped, "cars": w.cars} for w in options]}
    if not options:
        record.update(choice=None, by="rules", why="no open way on the map; the driving system routes it")
        return [goal], record
    shortest = options[0]
    if len(options) == 1 or ask_when == "never" or (ask_when != "always" and all(w.quiet for w in options)):
        why = ("the only open way" if len(options) == 1
               else "nothing reported on any way, so the shortest")
        record.update(choice=shortest.label, by="rules", why=why)
        return shortest.points[1:], record
    labels = tuple(w.label for w in options)
    schema = decision_schema(*labels)
    fallback = schema(choice=shortest.label, probs={l: float(l == shortest.label) for l in labels})
    started = time.monotonic()
    decision, answered = await ask_or(rt, TASK, prompt(vin, start, goal, options, speed_kmh), schema,
                                      fallback, system=SYSTEM)
    chosen = next(w for w in options if w.label == decision.choice)
    record.update(choice=chosen.label, by="gemini" if answered else "rules",
                  probs={k: round(v, 3) for k, v in decision.probs.items()},
                  why=("Gemini's choice" if answered else "Gemini did not answer; the shortest way"),
                  seconds=round(time.monotonic() - started, 2))
    log.info("route %s %s -> %s: %s (%.2f) by %s", vin, start, goal, chosen.label,
             decision.confidence, record["by"])
    return chosen.points[1:], record


# ---------- the offline stand-in ----------

_LINE = re.compile(r"^- (?P<label>.+?): (?P<m>\d+) m, .*?hazards: (?P<h>.*?); stopped cars: (?P<s>.*?); "
                   r"cars driving on it: (?P<c>.*)$")


def offline_choice(call) -> dict:
    """Scores each way from the prompt: length, plus a lot for hazards and stopped cars."""
    scores = {}
    for line in call.prompt.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        score = float(m["m"])
        score += 0 if m["h"] == "none reported" else 300 * (m["h"].count(";") + 1)
        score += 0 if m["s"] == "none" else 500 * (m["s"].count(",") + 1)
        score += 0 if m["c"] == "none" else 10 * (m["c"].count(",") + 1)
        scores[m["label"]] = score
    best = min(scores, key=scores.get)
    rest = max(1, len(scores) - 1)
    return {"choice": best, "probs": {k: (0.9 if k == best else 0.1 / rest) for k in scores}}
