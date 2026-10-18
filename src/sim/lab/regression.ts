// Flow 7 — Regression suite: run every approved library scenario and judge it. The release
// verdict is fragile if any scenario fails.

import type { FlowHost } from '#/sim/flows/host'
import { simulateSpec } from '#/sim/lab/generator'
import { judgeRun } from '#/sim/lab/judge'
import type { Scenario, TestRun } from '#/sim/records'

export type RegressionResult = { verdict: 'robust' | 'fragile'; runs: TestRun[] }

export function runRegression(host: FlowHost, specs: Scenario[]): RegressionResult {
  const runs: TestRun[] = specs.map((spec) => {
    const result = simulateSpec(host, spec)
    const judged = judgeRun(result.metrics, spec)
    return {
      scenarioId: spec.id,
      variant: spec,
      metrics: result.metrics,
      passed: judged.passed,
      failedCriteria: judged.failedCriteria,
    }
  })
  return { verdict: runs.some((r) => !r.passed) ? 'fragile' : 'robust', runs }
}
