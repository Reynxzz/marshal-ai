// Flow 6/7 — Judge a run purely from its metrics. AI never decides pass or fail.

import type { Scenario } from '#/sim/records'

export type JudgeResult = { passed: boolean; failedCriteria: string[] }

export function judgeRun(metrics: Record<string, number>, spec: Scenario): JudgeResult {
  const failedCriteria: string[] = []

  if (spec.expect.noContact && (metrics.contact ?? 0) !== 0) {
    failedCriteria.push('noContact')
  }
  if (
    spec.expect.clearsWithinS !== undefined &&
    (metrics.time_to_clear_s ?? Number.POSITIVE_INFINITY) > spec.expect.clearsWithinS
  ) {
    failedCriteria.push('clearsWithinS')
  }

  return { passed: failedCriteria.length === 0, failedCriteria }
}
