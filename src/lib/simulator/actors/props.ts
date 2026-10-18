// Props (TEAM_PLAN M6.1/M6.2/M7.2): the obstacle kinds the engine scans ahead, brakes for and
// reports to the parking planner. Pure data: footprints in metres, CARLA frame.

/** Prop kinds the engine spawns. */
export type PropKind = 'box' | 'worker' | 'cone' | 'barrel' | 'truck'

/** Footprint of a prop kind. */
export type PropFootprint = {
  /** Along the prop heading. */
  length: number
  width: number
  height: number
  /** Workers move; the scanner treats them as pedestrians. */
  moving: boolean
}

/** Footprints per kind (props.ts of the simulator). */
export const PROP_FOOTPRINTS: Record<PropKind, PropFootprint> = {
  box: { length: 1.2, width: 1.2, height: 1, moving: false },
  worker: { length: 0.6, width: 0.6, height: 1.7, moving: true },
  cone: { length: 0.4, width: 0.4, height: 0.7, moving: false },
  barrel: { length: 0.6, width: 0.6, height: 0.9, moving: false },
  truck: { length: 8, width: 2.5, height: 3, moving: false },
}

/** Props that lie flat enough to be ignored by the safety scan, metres (M7.2). */
export const FLAT_PROP_HEIGHT_M = 0.15

/**
 * Type guard for prop kinds.
 * @param kind - Candidate kind.
 * @returns True for box/worker/cone/barrel/truck.
 */
export function isPropKind(kind: string): kind is PropKind {
  return Object.hasOwn(PROP_FOOTPRINTS, kind)
}

/**
 * Footprint for a prop kind.
 * @param kind - Prop kind.
 * @returns Copy of the footprint.
 */
export function propFootprint(kind: PropKind): PropFootprint {
  return { ...PROP_FOOTPRINTS[kind] }
}
