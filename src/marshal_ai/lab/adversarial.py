"""Flow 7: Adversarial test agent.

From a passing scenario, Gemini proposes batches of variations and steers toward the
edge; the rule judge decides every verdict. A failure is shrunk to the smallest change
from the passing case, explained by Gemini and added to the regression suite.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from marshal_ai.lab.judge import judge
from marshal_ai.lab.runner import Runner, run_batch, to_test_run
from marshal_ai.lab.spec import ScenarioSpec
from marshal_ai.llm import to_prompt_json
from marshal_ai.records import Scenario, ScenarioStatus, TestRun

if TYPE_CHECKING:
    from marshal_ai.runtime import Runtime

BUDGET = 60
BATCH = 8

Light = Literal["noon", "dusk", "night"]
WeatherT = Literal["clear", "rain", "fog"]
Occluder = Literal["none", "forklift", "parked_car", "pallet_stack"]
SecondActor = Literal["none", "car_ahead_braking"]

RANGES = {
    "trigger_distance_m": (4.0, 15.0),
    "worker_speed_ms": (0.8, 2.5),
    "ego_speed_kmh": (5.0, 10.0),
}
CHOICES = {
    "light": ["noon", "dusk", "night"],
    "weather": ["clear", "rain", "fog"],
    "occluder": ["none", "forklift", "parked_car", "pallet_stack"],
    "second_actor": ["none", "car_ahead_braking"],
}
PARAMS = list(RANGES) + list(CHOICES)


class Variation(BaseModel):
    trigger_distance_m: float | None = None
    worker_speed_ms: float | None = None
    light: Light | None = None
    weather: WeatherT | None = None
    occluder: Occluder | None = None
    ego_speed_kmh: float | None = None
    second_actor: SecondActor | None = None

    def changes(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class VariationBatch(BaseModel):
    reasoning: str = Field(description="what the scores so far suggest and where to search next")
    variations: list[Variation]


class FailureExplanation(BaseModel):
    summary: str = Field(description="the failing case and how it differs from the passing one")
    likely_cause: str
    suggested_fix: str


SEARCH_SYSTEM = """You search for situations where a driverless plant car behaves unsafely. You get the
passing base case, the parameters you may vary with their ranges, and every run so far with its
metrics. Propose the next batch of variations that move toward failure (smaller gaps, contact, fast
near people). Change few parameters at a time so failures stay explainable. Give your reasoning."""

REPORT_SYSTEM = """You explain a simulation test failure to driving-system engineers in plain
language: what happened, how it differs from the passing case, the likely cause, and a concrete fix."""


def base_params(spec: ScenarioSpec) -> dict:
    worker = next((a for a in spec.actors if a.type.value == "worker"), None)
    occluder = "none"
    if worker and worker.start and worker.start.startswith("behind:"):
        occluder = worker.start.removeprefix("behind:")
    return {
        "trigger_distance_m": worker.trigger.ego_distance_m if worker and worker.trigger else None,
        "worker_speed_ms": worker.walk_speed_ms if worker else None,
        "light": spec.time_of_day.value,
        "weather": spec.weather.value,
        "occluder": occluder,
        "ego_speed_kmh": spec.ego.speed_kmh,
        "second_actor": "car_ahead_braking" if any(a.type.value == "car" and a.state == "braking"
                                                   for a in spec.actors) else "none",
    }


def clamp(changes: dict) -> dict:
    out = {}
    for k, v in changes.items():
        if k in RANGES:
            lo, hi = RANGES[k]
            out[k] = round(min(hi, max(lo, float(v))), 2)
        elif k in CHOICES and v in CHOICES[k]:
            out[k] = v
    return out


def apply_variation(base: ScenarioSpec, changes: dict, variant_id: str) -> ScenarioSpec:
    raw = copy.deepcopy(base.model_dump(mode="json"))
    raw["id"] = variant_id
    if "light" in changes:
        raw["time_of_day"] = changes["light"]
    if "weather" in changes:
        raw["weather"] = changes["weather"]
    if "ego_speed_kmh" in changes:
        raw["ego"]["speed_kmh"] = changes["ego_speed_kmh"]
    actors = raw["actors"]
    worker = next((a for a in actors if a["type"] == "worker"), None)
    if worker is not None:
        if "trigger_distance_m" in changes:
            worker["trigger"] = {"ego_distance_m": changes["trigger_distance_m"]}
        if "worker_speed_ms" in changes:
            worker["walk_speed_ms"] = changes["worker_speed_ms"]
        if "occluder" in changes:
            old = worker.get("start", "") or ""
            old_type = old.removeprefix("behind:") if old.startswith("behind:") else None
            occ = next((a for a in actors if old_type and a["type"] == old_type), None)
            anchor = occ["position"] if occ else (worker.get("position") or worker.get("start"))
            if occ is not None:
                actors.remove(occ)
            if changes["occluder"] == "none":
                worker["start"] = anchor
            else:
                actors.insert(0, {"type": changes["occluder"], "state": "parked", "position": anchor})
                worker["start"] = f"behind:{changes['occluder']}"
            worker.pop("position", None)
    if "second_actor" in changes:
        raw["actors"] = actors = [a for a in actors if not (a["type"] == "car" and a.get("state") == "braking")]
        if changes["second_actor"] == "car_ahead_braking":
            actors.append({"type": "car", "state": "braking", "position": base.ego.waypoints[0],
                           "trigger": {"ego_distance_m": 10}})
        if "min_gap_to_vehicle_m >= 1.0" not in raw["pass_criteria"] and changes["second_actor"] != "none":
            raw["pass_criteria"].append("min_gap_to_vehicle_m >= 1.0")
    return ScenarioSpec.model_validate(raw)


@dataclass
class SearchRun:
    changes: dict
    spec: ScenarioSpec
    run: TestRun


@dataclass
class SearchResult:
    scenario_id: str
    runs: list[SearchRun] = field(default_factory=list)
    failure: SearchRun | None = None
    minimal: SearchRun | None = None
    explanation: FailureExplanation | None = None
    reasoning: list[str] = field(default_factory=list)
    regression_id: str | None = None

    @property
    def robust(self) -> bool:
        return self.failure is None

    def report(self) -> str:
        if self.minimal is None:
            return (f"{self.scenario_id}: no failure in {len(self.runs)} runs; "
                    "robust within the tested ranges.")
        m = self.minimal
        metrics = ", ".join(f"{k} {v}" for k, v in m.run.metrics.items())
        text = (f"{m.spec.id} fails ({'; '.join(m.run.failed_criteria)}). Changes from the passing case: "
                f"{m.changes}. Metrics: {metrics}.")
        if self.explanation:
            text += (f" {self.explanation.summary} Likely cause: {self.explanation.likely_cause}. "
                     f"Suggested fix: {self.explanation.suggested_fix}")
        return text


class AdversarialTester:
    def __init__(self, rt: "Runtime", runner: Runner, budget: int = BUDGET, batch: int = BATCH, seed: int = 7):
        self.rt = rt
        self.runner = runner
        self.budget = budget
        self.batch = batch
        self.random = random.Random(seed)

    def _history(self, base: dict, runs: list[SearchRun]) -> list[dict]:
        return [{"changes": r.changes, "passed": r.run.passed, "failed": r.run.failed_criteria,
                 "margins": judge(r.spec, r.run.metrics).margins if r.run.metrics else {}}
                for r in runs[-24:]]

    def _propose(self, base_spec: ScenarioSpec, base: dict, runs: list[SearchRun], n: int) -> tuple[list[dict], str]:
        prompt = (f"Passing base case: {to_prompt_json(base_spec)}\nBase parameters: {base}\n"
                  f"You may vary: ranges {RANGES}, choices {CHOICES}. Only worker parameters apply "
                  f"when the scenario has a worker.\nRuns so far: {to_prompt_json(self._history(base, runs))}\n"
                  f"Propose {n} new variations.")
        seen = [r.changes for r in runs]
        try:
            batch = self.rt.llm.generate("adversarial.propose", prompt, VariationBatch, None, SEARCH_SYSTEM)
            proposals = [clamp(v.changes()) for v in batch.variations]
            reasoning = batch.reasoning
        except Exception as e:          # search keeps going on random samples if the model fails
            proposals, reasoning = [], f"model unavailable ({e}); random sampling"
        proposals = [p for p in proposals if p and p not in seen]
        while len(proposals) < n:       # top up with random samples near the edge
            p = self._random_changes(base)
            if p not in seen and p not in proposals:
                proposals.append(p)
        return proposals[:n], reasoning

    def _random_changes(self, base: dict) -> dict:
        keys = self.random.sample(PARAMS, k=self.random.choice([1, 2, 3]))
        out = {}
        for k in keys:
            if k in RANGES:
                if base.get(k) is None:
                    continue
                lo, hi = RANGES[k]
                out[k] = round(self.random.uniform(lo, hi), 1)
            else:
                out[k] = self.random.choice([c for c in CHOICES[k] if c != base.get(k)])
        return clamp(out)

    def _run(self, base_spec: ScenarioSpec, changes_list: list[dict], start_index: int) -> list[SearchRun]:
        specs = [apply_variation(base_spec, c, f"{base_spec.id}-v{start_index + i}")
                 for i, c in enumerate(changes_list)]
        outcomes = run_batch(self.runner, specs, self.batch)
        runs = []
        for c, spec, outcome in zip(changes_list, specs, outcomes):
            run = to_test_run(spec, outcome, scenario_id=base_spec.id, params=c)
            self.rt.store.put(run)
            runs.append(SearchRun(c, spec, run))
        return runs

    def _shrink(self, base_spec: ScenarioSpec, base: dict, failing: SearchRun, result: SearchResult) -> SearchRun:
        """Undo changes one at a time; keep each undo that still fails."""
        current = failing
        for key in list(failing.changes):
            if len(result.runs) >= self.budget:
                break
            trial = {k: v for k, v in current.changes.items() if k != key}
            if not trial:
                continue
            [r] = self._run(base_spec, [trial], len(result.runs))
            result.runs.append(r)
            if not r.run.passed:
                current = r
        return current

    def search(self, scenario_id: str) -> SearchResult:
        scenario = self.rt.store.get(Scenario, scenario_id)
        base_spec = ScenarioSpec.model_validate(scenario.spec)
        base = base_params(base_spec)
        result = SearchResult(scenario_id)
        [base_run] = self._run(base_spec, [{}], 0)
        if not base_run.run.passed:
            raise ValueError(f"{scenario_id} already fails; the search starts from a passing scenario")
        result.runs.append(base_run)
        while len(result.runs) < self.budget and result.failure is None:
            n = min(self.batch, self.budget - len(result.runs))
            proposals, reasoning = self._propose(base_spec, base, result.runs, n)
            result.reasoning.append(reasoning)
            runs = self._run(base_spec, proposals, len(result.runs))
            result.runs.extend(runs)
            failures = [r for r in runs if not r.run.passed]
            if failures:
                result.failure = min(failures, key=lambda r: len(r.changes))
        if result.failure is None:
            return result
        result.minimal = self._shrink(base_spec, base, result.failure, result)
        result.explanation = self._explain(base_spec, result.minimal)
        result.regression_id = self._add_to_suite(result)
        return result

    def _explain(self, base_spec: ScenarioSpec, minimal: SearchRun) -> FailureExplanation | None:
        prompt = (f"Passing case: {to_prompt_json(base_spec)}\nFailing variant: {to_prompt_json(minimal.spec)}\n"
                  f"Changes: {minimal.changes}\nMetrics: {minimal.run.metrics}\n"
                  f"Failed criteria: {minimal.run.failed_criteria}")
        try:
            return self.rt.llm.generate("adversarial.report", prompt, FailureExplanation, None, REPORT_SYSTEM)
        except Exception:
            return None

    def _add_to_suite(self, result: SearchResult) -> str:
        m = result.minimal
        scenario = Scenario(id=m.spec.id, spec=m.spec.model_dump(mode="json"),
                            source=f"adversarial search on {result.scenario_id}",
                            status=ScenarioStatus.IN_LIBRARY, tags=["regression"], notes=result.report())
        self.rt.store.put(scenario)
        return scenario.id
