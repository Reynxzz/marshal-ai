"""The loop between operations and the test lab: regression suite and release gate.

A driving system release is approved only when the whole suite passes, and only by an
engineer; Marshal produces the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from marshal_ai.lab.runner import Runner, run_batch, to_test_run
from marshal_ai.lab.spec import ScenarioSpec
from marshal_ai.records import Scenario, ScenarioStatus

if TYPE_CHECKING:
    from marshal_ai.runtime import Runtime


@dataclass
class ReleaseReport:
    release: str
    total: int
    passed: list[str]
    failed: dict[str, list[str]]                # scenario id -> failed criteria
    newly_failing: list[str] = field(default_factory=list)
    newly_passing: list[str] = field(default_factory=list)
    approved_by: str | None = None

    @property
    def blocked(self) -> bool:
        return bool(self.failed)

    def summary(self) -> str:
        head = (f"Release {self.release}: {self.total} tests, {len(self.passed)} passed, "
                f"{len(self.failed)} failed.")
        if self.blocked:
            head += f" {len(self.failed)} failing test{'s' if len(self.failed) > 1 else ''}, release blocked."
        else:
            head += " Ready for engineer approval." if not self.approved_by else f" Approved by {self.approved_by}."
        if self.newly_failing:
            head += f" Newly failing since last release: {', '.join(self.newly_failing)}."
        if self.newly_passing:
            head += f" Now passing: {', '.join(self.newly_passing)}."
        return head


class ReleaseGate:
    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self.history: list[ReleaseReport] = []

    def suite(self) -> list[Scenario]:
        return sorted((s for s in self.rt.store.list(Scenario) if s.status == ScenarioStatus.IN_LIBRARY),
                      key=lambda s: s.id)

    def evaluate(self, release: str, runner: Runner) -> ReleaseReport:
        scenarios = self.suite()
        specs = [ScenarioSpec.model_validate(s.spec) for s in scenarios]
        outcomes = run_batch(runner, specs)
        passed, failed = [], {}
        for spec, outcome in zip(specs, outcomes):
            run = to_test_run(spec, outcome, params={"release": release})
            self.rt.store.put(run)
            if run.passed:
                passed.append(spec.id)
            else:
                failed[spec.id] = run.failed_criteria
        report = ReleaseReport(release, len(specs), passed, failed)
        if self.history:
            prev = self.history[-1]
            report.newly_failing = sorted(set(failed) - set(prev.failed))
            report.newly_passing = sorted(set(prev.failed) & set(passed))
        self.history.append(report)
        return report

    def approve(self, report: ReleaseReport, engineer: str) -> ReleaseReport:
        if report.blocked:
            raise PermissionError(f"release {report.release} is blocked by {len(report.failed)} failing tests")
        report.approved_by = engineer
        return report
