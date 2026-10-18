// Mapping from simulation state to the 7-flow stage shown in the dashboard.

import type { CarState } from './records'

export function stageFor(state: CarState, hasOpenCase = false): number {
  if (hasOpenCase) return 2
  switch (state) {
    case 'line_end':
    case 'waiting_dispatch':
      return 1
    case 'held':
      return 2
    case 'inspection_gate':
      return 3
    case 'test_track':
      return 4
    case 'report':
    case 'rework':
    case 'yard':
    case 'shipped':
      return 5
    default:
      return 1
  }
}

/** A car counts as finished once it is parked in the yard, shipped, or done reworking. */
export function isFinished(state: CarState): boolean {
  return state === 'yard' || state === 'shipped' || state === 'rework'
}
