"""Flow 7: Adversarial test agent.

From a passing scenario, Gemini proposes batches of variations and steers toward the
edge; the rule judge decides every verdict. A failure is shrunk to the smallest change
from the passing case, explained by Gemini and added to the regression suite.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from marshal_ai.lab.judge import judge
from marshal_ai.lab.reference import unfair_reasons
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


class SpeedFix(BaseModel):
    zone: str = Field(description="the plant zone the speed limit applies in")
    time_of_day: list[Light] = Field(description="when it applies")
    max_speed_kmh: float = Field(gt=0, le=10)


class FailureExplanation(BaseModel):
    summary: str = Field(description="the failing case and how it differs from the passing one")
    likely_cause: str
    suggested_fix: str = Field(description="one concrete change to the driving system, in a sentence")
    speed_fix: SpeedFix | None = Field(default=None, description="the fix as a speed limit, when a slower "
                                       "approach would prevent the failure; null otherwise")


class FailureReport(BaseModel):
    """The design's failure report (flow 7): the facts from code, the cause and fix from Gemini,
    and the fix tried by code before anyone is asked to believe it."""

    variant: str
    conditions: str
    measured: list[str] = Field(description="each failed criterion: what was measured against its limit")
    passes_when: list[str] = Field(description="single changes back to the passing case that make it pass")
    likely_cause: str | None = None
    suggested_fix: str | None = None
    speed_fix: SpeedFix | None = None
    fix_check: str | None = None        # what happened when the fix was tried, or why it was not
    clip_uri: str | None = None

    def text(self) -> str:
        out = f"{self.variant} fails: {self.conditions}, {'; '.join(self.measured)}."
        if self.passes_when:
            out += f" It passes {' or '.join(self.passes_when)}."
        if self.likely_cause:
            out += f" Likely cause: {self.likely_cause.rstrip('.')}."
        if self.suggested_fix:
            out += f" Suggested fix: {self.suggested_fix.rstrip('.')}."
        if self.fix_check:
            out += f" {self.fix_check}"
        if self.clip_uri:
            out += f" Clip: {self.clip_uri}"
        return out


OCCLUDER_WORDS = {"forklift": "a forklift", "parked_car": "a parked car", "pallet_stack": "a pallet stack"}


def conditions_text(params: dict) -> str:
    """A variant's conditions in words, as the design's example report states them."""
    parts = [f"at {params['light']}" if params.get("light") != "noon" else "at noon"]
    if params.get("weather") and params["weather"] != "clear":
        parts.append(f"in {params['weather']}")
    worker = []
    if params.get("trigger_distance_m") is not None:
        worker.append(f"starting to move when the car is {params['trigger_distance_m']:g} m away")
    if params.get("worker_speed_ms") is not None:
        worker.append(f"at {params['worker_speed_ms']:g} m/s")
    if params.get("occluder") not in (None, "none"):
        worker.append(f"from behind {OCCLUDER_WORDS[params['occluder']]}")
    if worker:
        parts.append("with the worker " + " ".join(worker))
    if params.get("second_actor") == "car_ahead_braking":
        parts.append("with a car braking ahead")
    parts.append(f"the car driving at {params['ego_speed_kmh']:g} km/h")
    return ", ".join(parts)


# How each varied parameter reads in "It passes with ...": its name and unit.
PARAM_WORDS = {"ego_speed_kmh": ("the car's speed", " km/h"), "trigger_distance_m": ("the trigger distance", " m"),
               "worker_speed_ms": ("the worker's speed", " m/s")}


def passes_text(key: str, value) -> str:
    """One single change that makes the failing case pass, e.g. "with the car's speed back at 8 km/h"."""
    if key == "occluder":
        return f"with the worker behind {OCCLUDER_WORDS[value]}" if value in OCCLUDER_WORDS else "with nothing hiding the worker"
    if key == "second_actor":
        return "without the car braking ahead" if value == "none" else "with the car braking ahead"
    if key in ("light", "weather"):
        return f"with {key} back at {value}"
    name, unit = PARAM_WORDS.get(key, (key.replace("_", " "), ""))
    return f"with {name} back at {value:g}{unit}" if isinstance(value, float) else f"with {name} back at {value}{unit}"


def measured_text(spec: ScenarioSpec, metrics: dict) -> list[str]:
    out = []
    for text, c in zip(spec.pass_criteria, spec.criteria()):
        if c.holds(metrics) if c.metric in metrics else False:
            continue
        if c.op == "flag":
            out.append({"no_contact": "the car made contact", "reached_goal": "the car did not reach its goal"}
                       .get(c.metric, f"{c.metric} failed"))
        else:
            unit = " km/h" if c.metric.endswith("_kmh") else " s" if c.metric.endswith("_s") else " m"
            out.append(f"{c.metric.replace('_', ' ').removesuffix(' kmh').removesuffix(' m').removesuffix(' s')} "
                       f"{metrics.get(c.metric)}{unit} (limit {c.op} {c.value:g}{unit})")
    return out


SEARCH_SYSTEM = """You search for situations where a driverless plant car behaves unsafely. You get the
passing base case, the parameters you may vary with their ranges, and every run so far with its
metrics. Propose the next batch of variations that move toward failure (smaller gaps, contact, fast
near people). Change few parameters at a time so failures stay explainable. Only situations a careful
driver could survive count: variations that even a careful reference driver fails are skipped and
listed as unavoidable, so do not propose more of them. Give your reasoning."""

REPORT_SYSTEM = """You explain a simulation test failure to driving-system engineers in plain
language: what happened, how it differs from the passing case, the likely cause, and one concrete fix
to the driving system. Name the zone, the conditions and the number, as in "reduce speed near
finishing_corner at night to 5 km/h"; never a general advice such as "improve detection". When a
slower approach would prevent the failure, also give the fix as speed_fix, using a zone the car
drives through; the fix will be tried in the simulator before anyone acts on it."""


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
    failure_report: FailureReport | None = None
    necessary: list[str] = field(default_factory=list)      # changes whose undo made the case pass
    reasoning: list[str] = field(default_factory=list)
    regression_id: str | None = None
    unavoidable: list[dict] = field(default_factory=list)   # skipped: even a careful driver fails them

    @property
    def robust(self) -> bool:
        return self.failure is None

    def report(self) -> str:
        skipped = (f" {len(self.unavoidable)} proposed variations were skipped as unavoidable "
                   "(a careful driver fails them too)." if self.unavoidable else "")
        if self.minimal is None:
            return (f"{self.scenario_id}: no failure in {len(self.runs)} runs; "
                    "robust within the tested ranges." + skipped)
        if self.failure_report is not None:
            return self.failure_report.text() + skipped
        m = self.minimal
        metrics = ", ".join(f"{k} {v}" for k, v in m.run.metrics.items())
        text = (f"{m.spec.id} fails ({'; '.join(m.run.failed_criteria)}). Changes from the passing case: "
                f"{m.changes}. Metrics: {metrics}.")
        if self.explanation:
            text += (f" {self.explanation.summary} Likely cause: {self.explanation.likely_cause}. "
                     f"Suggested fix: {self.explanation.suggested_fix}")
        return text + skipped


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

    def _fair(self, base_spec: ScenarioSpec, changes: dict, result: "SearchResult") -> bool:
        """Code, not the model, decides whether a variation is a fair test (see lab/reference.py)."""
        why = unfair_reasons(apply_variation(base_spec, changes, f"{base_spec.id}-v0"), self.rt.map)
        if why:
            result.unavoidable.append({"changes": changes, "why": why[0]})
        return not why

    def _propose(self, base_spec: ScenarioSpec, base: dict, runs: list[SearchRun], n: int,
                 result: "SearchResult") -> tuple[list[dict], str]:
        skipped = [{"changes": u["changes"], "unavoidable": u["why"]} for u in result.unavoidable[-12:]]
        prompt = (f"Passing base case: {to_prompt_json(base_spec)}\nBase parameters: {base}\n"
                  f"You may vary: ranges {RANGES}, choices {CHOICES}. Only worker parameters apply "
                  f"when the scenario has a worker.\nRuns so far: {to_prompt_json(self._history(base, runs))}\n"
                  + (f"Skipped as unavoidable: {to_prompt_json(skipped)}\n" if skipped else "")
                  + f"Propose {n} new variations.")
        seen = [r.changes for r in runs] + [u["changes"] for u in result.unavoidable]
        try:
            batch = self.rt.llm.generate("adversarial.propose", prompt, VariationBatch, None, SEARCH_SYSTEM)
            proposals = [clamp(v.changes()) for v in batch.variations]
            reasoning = batch.reasoning
        except Exception as e:          # search keeps going on random samples if the model fails
            proposals, reasoning = [], f"model unavailable ({e}); random sampling"
        proposals = [p for p in proposals if p and p not in seen]
        proposals = [p for p in proposals if self._fair(base_spec, p, result)]
        tries = 0
        while len(proposals) < n and tries < 400:   # top up with random samples near the edge
            tries += 1
            p = self._random_changes(base)
            if p and p not in seen and p not in proposals and self._fair(base_spec, p, result):
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

    def _run(self, base_spec: ScenarioSpec, changes_list: list[dict], start_index: int,
             reason: str) -> list[SearchRun]:
        specs = [apply_variation(base_spec, c, f"{base_spec.id}-v{start_index + i}")
                 for i, c in enumerate(changes_list)]
        outcomes = run_batch(self.runner, specs, self.batch)
        runs = []
        for c, spec, outcome in zip(changes_list, specs, outcomes):
            run = to_test_run(spec, outcome, scenario_id=base_spec.id, params=c).model_copy(
                update={"reason": reason})
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
            if not trial or not self._fair(base_spec, trial, result):
                continue
            [r] = self._run(base_spec, [trial], len(result.runs),
                            reason=f"shrinking {current.spec.id}: undo {key}, to see whether it still fails")
            result.runs.append(r)
            if not r.run.passed:
                current = r
            else:
                result.necessary.append(key)
        if len(current.changes) == 1:               # undoing the one change is the passing case itself
            result.necessary = list(current.changes)
        return current

    def search(self, scenario_id: str) -> SearchResult:
        scenario = self.rt.store.get(Scenario, scenario_id)
        base_spec = ScenarioSpec.model_validate(scenario.spec)
        base = base_params(base_spec)
        result = SearchResult(scenario_id)
        [base_run] = self._run(base_spec, [{}], 0, reason="the passing case the search starts from")
        if not base_run.run.passed:
            raise ValueError(f"{scenario_id} already fails; the search starts from a passing scenario")
        result.runs.append(base_run)
        while len(result.runs) < self.budget and result.failure is None:
            n = min(self.batch, self.budget - len(result.runs))
            proposals, reasoning = self._propose(base_spec, base, result.runs, n, result)
            result.reasoning.append(reasoning)
            if not proposals:
                break                   # nothing fair left to try
            runs = self._run(base_spec, proposals, len(result.runs), reason=reasoning)
            result.runs.extend(runs)
            failures = [r for r in runs if not r.run.passed]
            if failures:
                result.failure = min(failures, key=lambda r: len(r.changes))
        if result.failure is None:
            return result
        result.minimal = self._shrink(base_spec, base, result.failure, result)
        result.failure_report = self._report(base_spec, base, result)
        result.regression_id = self._add_to_suite(result)
        return result

    def _report(self, base_spec: ScenarioSpec, base: dict, result: SearchResult) -> FailureReport:
        m = result.minimal
        params = {**base, **m.changes}
        passes_when = [passes_text(k, base[k]) for k in result.necessary if k in base and base[k] is not None]
        report = FailureReport(variant=m.spec.id, conditions=conditions_text(params),
                               measured=measured_text(m.spec, m.run.metrics), passes_when=passes_when,
                               clip_uri=m.run.clip_uri)
        result.explanation = self._explain(base_spec, m, report)
        if result.explanation is None:
            return report
        fix = result.explanation.speed_fix
        if fix is not None and fix.zone not in self.rt.map.zones:
            fix = None                       # a zone the plant does not have cannot be tried
        report = report.model_copy(update={"likely_cause": result.explanation.likely_cause,
                                           "suggested_fix": result.explanation.suggested_fix,
                                           "speed_fix": fix})
        return report.model_copy(update={"fix_check": self._try_fix(base_spec, m.spec, fix)})

    def _explain(self, base_spec: ScenarioSpec, minimal: SearchRun,
                 report: FailureReport) -> FailureExplanation | None:
        prompt = (f"Passing case: {to_prompt_json(base_spec)}\nFailing variant: {to_prompt_json(minimal.spec)}\n"
                  f"Changes: {minimal.changes}\nMetrics: {minimal.run.metrics}\n"
                  f"Failed criteria: {minimal.run.failed_criteria}\n"
                  f"Measured: {report.measured}\nIt passes: {report.passes_when or 'not established'}\n"
                  f"Zones the car drives through: {self._route_zones(base_spec)}")
        try:
            return self.rt.llm.generate("adversarial.report", prompt, FailureExplanation, None, REPORT_SYSTEM)
        except Exception:
            return None

    def _route_zones(self, spec: ScenarioSpec) -> list[str]:
        names = [spec.ego.waypoints[0]]
        for a, b in zip(spec.ego.waypoints, spec.ego.waypoints[1:]):
            names += self.rt.map.route(a, b)[1:]
        return sorted({self.rt.map.points[n].zone for n in names})

    def _try_fix(self, base_spec: ScenarioSpec, failing: ScenarioSpec, fix: SpeedFix | None) -> str | None:
        """Run the failing case, and the passing one, with the fix in the driving system.

        A fix the lab can state as a speed limit it can also try, which is the point of asking for
        one: the report then says whether the fix works rather than only proposing it.
        """
        if fix is None:
            return None
        driver = getattr(self.runner, "driver", None)
        if driver is None:
            return "The fix was not tried: this runner cannot change the driving system."
        caps = {**driver.zone_speed_caps_kmh, **{f"{fix.zone}@{t}": fix.max_speed_kmh for t in fix.time_of_day}}
        fixed = self.runner.with_driver(replace(driver, name=f"{driver.name}+fix", zone_speed_caps_kmh=caps))
        failing_now, base_now = (judge(s, fixed.run(s).metrics) for s in (failing, base_spec))
        when = ", ".join(fix.time_of_day)
        tried = f"With {fix.max_speed_kmh:g} km/h in {fix.zone} at {when}"
        if failing_now.passed and base_now.passed:
            return f"Fix tried in the simulator: {tried.lower()}, the failing case passes and the passing case still passes."
        if not failing_now.passed:
            return f"Fix tried in the simulator: {tried.lower()}, the case still fails ({'; '.join(failing_now.failed)})."
        return f"Fix tried in the simulator: {tried.lower()}, the case passes but the passing case now fails."

    def _add_to_suite(self, result: SearchResult) -> str:
        m = result.minimal
        scenario = Scenario(id=m.spec.id, spec=m.spec.model_dump(mode="json"),
                            source=f"adversarial search on {result.scenario_id}",
                            status=ScenarioStatus.IN_LIBRARY, tags=["regression"], notes=result.report(),
                            failure_report=result.failure_report.model_dump(mode="json")
                            if result.failure_report else None)
        self.rt.store.put(scenario)
        return scenario.id
