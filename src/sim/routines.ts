// Routine R-12: the fixed functional test sequence run on the test track.
// Telemetry is always "healthy" — a dead lamp only shows up through the camera (T07).

import type { Telemetry } from './events'

export type RoutineStep = { name: string; durationS: number; telemetry: Telemetry }

export const ROUTINE_R12: RoutineStep[] = [
  { name: 'headlights_on', durationS: 3, telemetry: { lamp: 'headlights', on: true } },
  { name: 'headlights_off', durationS: 2, telemetry: { lamp: 'headlights', on: false } },
  { name: 'brake_lights', durationS: 3, telemetry: { lamp: 'brake_lights', on: true } },
  { name: 'reverse', durationS: 3, telemetry: { gear: 'R' } },
  { name: 'brake_test', durationS: 4, telemetry: { brakeDistanceM: 15, speedKmh: 0 } },
]

export const ROUTINES: Record<string, RoutineStep[]> = {
  'R-12': ROUTINE_R12,
}

/** Resolve routine steps by name, optionally filtered to a subset of step names. */
export function routineFor(name?: string, stepNames?: string[]): RoutineStep[] {
  if (!name) return []
  const base = ROUTINES[name] ?? ROUTINE_R12
  if (!stepNames || stepNames.length === 0) return base
  return base.filter((s) => stepNames.includes(s.name))
}
