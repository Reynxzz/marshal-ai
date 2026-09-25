"""Rule judge: code, not Gemini, decides pass or fail, so verdicts are repeatable."""

from __future__ import annotations

from dataclasses import dataclass

from marshal_ai.lab.spec import ScenarioSpec


@dataclass
class Judgement:
    passed: bool
    failed: list[str]          # the criteria that did not hold, as written in the spec
    margins: dict[str, float]  # how far each numeric criterion is from its limit (negative = failing)


def judge(spec: ScenarioSpec, metrics: dict[str, float]) -> Judgement:
    failed, margins = [], {}
    for text, c in zip(spec.pass_criteria, spec.criteria()):
        if c.metric not in metrics:
            failed.append(f"{text} (not measured)")
            continue
        if not c.holds(metrics):
            failed.append(text)
        if c.op != "flag":
            actual = metrics[c.metric]
            margins[c.metric] = round(actual - c.value if c.op in (">=", ">") else c.value - actual, 2)
    return Judgement(not failed, failed, margins)
