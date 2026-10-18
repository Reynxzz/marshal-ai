// Routine R-12 (TEAM_PLAN M5.2): the six test-track steps with their planned durations, lamp
// states and telemetry. Pure data: the engine runs the sequence, drives the motion steps and
// emits `track.step`; faults never change the telemetry (M5.3 keeps it nominal).

import type { CarLamps } from './car'

/** Routine identifier used by the flows. */
export const R12_ROUTINE = 'R-12'

/** Distance the reverse step covers, metres (TEAM_PLAN sample value). */
export const R12_REVERSE_M = 3.02
/** Speed the brake test reaches before the hard stop, km/h. */
export const R12_BRAKE_TEST_KMH = 20
/** Indicator blink half-period, seconds (TEAM_PLAN M5.2 step 2). */
export const R12_BLINK_PERIOD_S = 1.2

/** One R-12 step. */
export type RoutineStepSpec = {
  step: number
  name: string
  /** Planned seconds; motion steps end on their own signal, this is the timeout guard. */
  duration_s: number
  /** Lamp state while the step runs. */
  lamps: CarLamps
  /** Telemetry payload; the engine overrides measured fields. */
  telemetry: Record<string, unknown>
}

const OFF: CarLamps = {
  headlights: false,
  brake_lights: false,
  reverse: false,
  indicator_left: false,
  indicator_right: false,
}

function lamps(state: Partial<CarLamps>): CarLamps {
  return { ...OFF, ...state }
}

/** The six R-12 steps in order (TEAM_PLAN M5.2). */
export const R12_STEPS: readonly RoutineStepSpec[] = [
  {
    step: 1,
    name: 'headlights',
    duration_s: 8,
    lamps: lamps({ headlights: true }),
    telemetry: { headlights: 'ON' },
  },
  {
    step: 2,
    name: 'indicators',
    duration_s: 10,
    lamps: lamps({ indicator_left: true }),
    telemetry: { indicator_left: 'BLINK', indicator_right: 'BLINK' },
  },
  {
    step: 3,
    name: 'brake_lights',
    duration_s: 8,
    lamps: lamps({ brake_lights: true }),
    telemetry: { brake_signal: true, brake_lamps: 'ON' },
  },
  {
    step: 4,
    name: 'reverse',
    duration_s: 25,
    lamps: lamps({ reverse: true }),
    telemetry: { gear: 'R', distance_m: R12_REVERSE_M, lateral_m: 0.05 },
  },
  {
    step: 5,
    name: 'brake_test',
    duration_s: 20,
    lamps: lamps({ brake_lights: true }),
    telemetry: { start_speed_kmh: R12_BRAKE_TEST_KMH, stopping_distance_m: 0 },
  },
  {
    step: 6,
    name: 'full_lock',
    // shortcut: the lock arcs are not driven (no open area modelled); the radii are recorded.
    duration_s: 3,
    lamps: lamps({}),
    telemetry: { radius_left_m: 5.4, radius_right_m: 5.5 },
  },
]

/** Lamp state with everything off. */
export function offLamps(): CarLamps {
  return { ...OFF }
}

/**
 * Steps to run: all of R-12, or the given subset in table order (rework re-checks, M5.6).
 * @param subset - Step numbers, e.g. [1, 3].
 * @returns The step specs to run.
 */
export function r12Steps(subset?: readonly number[]): RoutineStepSpec[] {
  if (!subset) return [...R12_STEPS]
  return R12_STEPS.filter((step) => subset.includes(step.step))
}
