"""Flow 6: Scenario generator.

Text (a supervisor request or an assistance case) -> Gemini writes a spec -> validate
against the plant map -> fix loop (3 attempts) -> compile -> dry run -> engineer review
-> test library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from marshal_ai.lab.compiler import compile_spec
from marshal_ai.lab.reference import unfair_reasons
from marshal_ai.lab.runner import Runner
from marshal_ai.lab.spec import FLAG_CRITERIA, METRICS, ActorType, ScenarioSpec, TimeOfDay, Weather
from marshal_ai.lab.validate import validate_spec
from marshal_ai.records import Scenario, ScenarioStatus

if TYPE_CHECKING:
    from marshal_ai.runtime import Runtime

MAX_FIXES = 3

EXAMPLE_SPEC = {
    "id": "SC-0142",
    "source": "supervisor request",
    "map_zone": "finishing_corner",
    "time_of_day": "dusk",
    "weather": "clear",
    "ego": {"route": "line_end->finishing", "speed_kmh": 8},
    "actors": [
        {"type": "forklift", "state": "parked", "position": "finishing_corner.p3"},
        {"type": "worker", "start": "behind:forklift", "walk_speed_ms": 1.3,
         "trigger": {"ego_distance_m": 9}},
    ],
    "pass_criteria": ["no_contact", "min_gap_to_person_m >= 1.5", "speed_near_person_kmh <= 5"],
}

# A case from the assistance desk (flow 2) recreated as a test: the design's ops-to-lab loop. Shown
# beside the supervisor example when the request comes from a case; without it Gemini put the box
# where the car starts on all three tries at scene 3's incident.
INCIDENT_EXAMPLE_SPEC = {
    "id": "SC-0150",
    "source": "assistance case AC-VIN-123-1",
    "description": "A box in the lane at the test track with a worker beside it; the car stopped and waited.",
    "map_zone": "test_track",
    "time_of_day": "noon",
    "weather": "clear",
    "ego": {"route": "inspection_gate->test_track->report_point", "speed_kmh": 8},
    "actors": [
        {"type": "box", "state": "stationary", "position": "test_track"},
        {"type": "worker", "state": "stationary", "position": "test_track"},
    ],
    "pass_criteria": ["no_contact", "min_gap_to_person_m >= 1.5", "speed_near_person_kmh <= 5"],
}

SYSTEM = """You turn plain-language plant situations into scenario specs for simulation tests of a
driverless car system. Output one JSON object only, never code. Use only the named map points, zones,
actor types, times of day, weather and pass criteria you are given, and only the fields the examples
use. Plant speed limit is 10 km/h. The car starts at the first point of its route, so never place an
actor there: to recreate something at point X, start the route at a point before X.
A worker who steps out from behind something uses "start": "behind:<actor type>" and needs
walk_speed_ms and a trigger; only a forklift, pallet stack or parked car can hide a person. A person
near a smaller object, such as a box, stands at that object's point. An unknown object blocking the lane (a dropped box, debris) is a "box"
actor with a position on the ego route; the car must stop before it, so do not require reached_goal.
Always include pass criteria that capture what safe means here, and that a careful driver could meet:
the car has to drive its route, so never limit its overall speed to near zero."""


@dataclass
class GenerationResult:
    scenario: Scenario
    attempts: int
    errors: list[str] = field(default_factory=list)
    code: str | None = None
    dry_run_metrics: dict | None = None

    @property
    def ok(self) -> bool:
        return self.scenario.status == ScenarioStatus.DRY_RUN_OK


def map_context(rt: "Runtime") -> dict:
    m = rt.map
    return {
        "zones": m.zones,
        "points_by_zone": {z: m.points_in_zone(z) for z in m.zones},
        "actor_types": [a.value for a in ActorType],
        "actor_states": ["parked", "stationary", "moving", "crossing", "braking"],
        "time_of_day": [t.value for t in TimeOfDay],
        "weather": [w.value for w in Weather],
        "pass_criteria": sorted(FLAG_CRITERIA) + [f"{x} <op> <number>" for x in sorted(METRICS)],
    }


class ScenarioGenerator:
    def __init__(self, rt: "Runtime", runner: Runner):
        self.rt = rt
        self.runner = runner

    def _next_id(self) -> str:
        existing = [int(s.id.split("-")[1]) for s in self.rt.store.list(Scenario) if s.id.count("-") == 1]
        return f"SC-{max(existing, default=141) + 1:04d}"

    def generate(self, request: str, source: str = "supervisor request") -> GenerationResult:
        scenario_id = self._next_id()
        context = json.dumps(map_context(self.rt), indent=1)
        examples = [EXAMPLE_SPEC] + ([INCIDENT_EXAMPLE_SPEC] if source.startswith("assistance") else [])
        prompt = (f"Situation: {request}\n\nPlant map and allowed values:\n{context}\n\n"
                  + "".join(f"Example of a valid spec:\n{json.dumps(e, indent=1)}\n\n" for e in examples)
                  + f"Write the spec. Use id {scenario_id} and source {source!r}.")
        raw: dict = {}
        errors: list[str] = []
        attempts = 0
        while attempts <= MAX_FIXES:
            attempts += 1
            if errors:
                prompt_now = (f"{prompt}\n\nYour previous spec:\n{json.dumps(raw, indent=1)}\n"
                              f"It has these errors; fix exactly these:\n- " + "\n- ".join(errors))
            else:
                prompt_now = prompt
            try:
                raw = self.rt.llm.generate("scenario.spec", prompt_now, None, None, SYSTEM)
            except Exception as e:
                errors = [f"model output unusable: {e}"]
                continue
            raw = dict(raw, id=scenario_id, source=source)
            check = validate_spec(raw, self.rt.map)
            if not check.ok:
                errors = check.errors
                continue
            spec = check.spec
            errors = unfair_reasons(spec, self.rt.map)      # criteria no careful driver could meet
            if errors:
                continue
            code = self.runner.compile(spec) if hasattr(self.runner, "compile") else compile_spec(spec, self.rt.map)
            dry = self.runner.run(spec)
            errors = self._dry_run_errors(spec, dry)
            if errors:
                continue
            scenario = Scenario(id=spec.id, spec=spec.model_dump(mode="json"), source=source,
                                status=ScenarioStatus.DRY_RUN_OK, notes=request,
                                tags=["from-incident"] if source.startswith("assistance") else [])
            self.rt.store.put(scenario)
            return GenerationResult(scenario, attempts, [], code, dry.metrics)

        scenario = Scenario(id=scenario_id, spec=raw, source=source, status=ScenarioStatus.REJECTED,
                            notes="Needs an engineer. Last errors: " + "; ".join(errors))
        self.rt.store.put(scenario)
        return GenerationResult(scenario, attempts, errors)

    @staticmethod
    def _dry_run_errors(spec: ScenarioSpec, dry) -> list[str]:
        if dry.error:
            return [f"dry run failed: {dry.error}"]
        errors = []
        for i, a in enumerate(spec.actors):
            if a.trigger is not None and i not in dry.triggered:
                errors.append(f"actors.{i}: trigger at {a.trigger.ego_distance_m} m never fired in the dry run; "
                              "place the actor on or next to the ego route")
        blocked = any(a.type == ActorType.BOX for a in spec.actors)     # the car is meant to stop
        if not blocked and not dry.metrics.get("reached_goal") and "reached_goal" not in spec.pass_criteria \
                and dry.metrics.get("no_contact"):
            errors.append("dry run: ego never reached the end of its route; shorten the route")
        return errors

    def review(self, scenario_id: str, approve: bool, engineer: str, note: str = "") -> Scenario:
        """An engineer reviews the spec; only approved specs enter the test library."""
        scenario = self.rt.store.get(Scenario, scenario_id)
        if scenario is None or scenario.status != ScenarioStatus.DRY_RUN_OK:
            raise ValueError(f"{scenario_id} is not waiting for review")
        scenario = scenario.model_copy(update={
            "status": ScenarioStatus.IN_LIBRARY if approve else ScenarioStatus.REJECTED,
            "owner": engineer, "notes": (scenario.notes + (f" | {note}" if note else "")).strip(" |"),
        })
        self.rt.store.put(scenario)
        return scenario
