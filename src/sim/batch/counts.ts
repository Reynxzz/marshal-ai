// Flow counts and progress helpers (T13).

import type { FlowCounts } from '../records'

export function emptyCounts(): FlowCounts {
  return {
    flow1: { released: 0, waiting: 0, inYard: 0, shipped: 0 },
    flow2: { cases: 0, open: 0, resolved: 0, escalated: 0 },
    flow3: { pass: 0, review: 0, fail: 0 },
    flow4: { pass: 0, fail: 0, disagreements: 0 },
    flow5: { pass: 0, rework: 0, shipped: 0 },
    flow6: { testRequests: 0, generated: 0, status: 'not_triggered' },
    flow7: { runs: 0, failures: 0, status: 'not_triggered' },
  }
}

export function computeProgress(finished: number, requested: number): number {
  if (requested <= 0) return 0
  return Math.round((finished / requested) * 100)
}
