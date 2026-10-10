"""The loop between operations and the test lab: regression suite and release gate.

A driving system release is approved only when the whole suite passes, and only by an
engineer; Marshal produces the evidence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from marshal_ai.lab.runner import PARALLEL, Runner, run_batch, to_test_run
from marshal_ai.lab.spec import ScenarioSpec
from marshal_ai.records import ReleaseReport, Scenario, ScenarioStatus

if TYPE_CHECKING:
    from marshal_ai.runtime import Runtime

__all__ = ["ReleaseGate", "ReleaseReport"]


class ReleaseGate:
    """Each report is stored, so it also becomes a BigQuery history row when history is on. The
    release before is read back from the store or, when this process never saw it, from history."""

    def __init__(self, rt: "Runtime"):
        self.rt = rt

    def suite(self) -> list[Scenario]:
        return sorted((s for s in self.rt.store.list(Scenario) if s.status == ScenarioStatus.IN_LIBRARY),
                      key=lambda s: s.id)

    def report(self, release: str) -> ReleaseReport | None:
        """The latest evaluation of a release."""
        return self.rt.store.get(ReleaseReport, release)

    def previous(self, release: str) -> ReleaseReport | None:
        """The most recently evaluated other release: this store's, or history's if newer or alone."""
        found = [r for r in self.rt.store.list(ReleaseReport) if r.release != release]
        row = self.rt.history.latest("ReleaseReport", exclude_key=release)
        if row is not None:
            found.append(ReleaseReport.model_validate(row))
        return max(found, key=lambda r: r.evaluated_at, default=None)

    def evaluate(self, release: str, runner: Runner, parallel: int = PARALLEL) -> ReleaseReport:
        """`parallel` is 1 on a single CARLA server: concurrent runs there corrupt each other."""
        scenarios = self.suite()
        specs = [ScenarioSpec.model_validate(s.spec) for s in scenarios]
        outcomes = run_batch(runner, specs, parallel)
        passed, failed = [], {}
        for spec, outcome in zip(specs, outcomes):
            run = to_test_run(spec, outcome, params={"release": release})
            self.rt.store.put(run)
            if run.passed:
                passed.append(spec.id)
            else:
                failed[spec.id] = run.failed_criteria
        report = ReleaseReport(release=release, total=len(specs), passed=passed, failed=failed)
        if prev := self.previous(release):
            report.previous_release = prev.release
            report.newly_failing = sorted(set(failed) - set(prev.failed))
            report.newly_passing = sorted(set(prev.failed) & set(passed))
        self.rt.store.put(report)
        return report

    def approve(self, report: ReleaseReport, engineer: str) -> ReleaseReport:
        if report.blocked:
            raise PermissionError(f"release {report.release} is blocked by {len(report.failed)} failing tests")
        report.approved_by = engineer
        self.rt.store.put(report)
        return report
