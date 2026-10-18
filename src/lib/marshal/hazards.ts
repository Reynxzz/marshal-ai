// Hazard staging for Scene A (TEAM_PLAN M9.4): a forklift-unloading scene at the finishing
// corner. Offsets are in the point frame (+forward along the point yaw, +left) and rotated by
// the point yaw. The DrivingSystem has no "sign" prop kind, so the warning sign is a cone.

import type { DrivingSystem } from './types'

/** Where hazards can be staged. */
export type HazardSite = 'finishing_corner'

/** Hazard presets. */
export type HazardPreset = 'forklift_unloading'

/** Prop kinds accepted by `DrivingSystem.spawnProp`. */
export type HazardPropKind = Parameters<DrivingSystem['spawnProp']>[1]

/** One prop of a preset in the point frame. */
export type HazardProp = {
  id: string
  kind: HazardPropKind
  /** Metres along the point heading. */
  forward: number
  /** Metres to the left of the point heading. */
  left: number
  /** Degrees relative to the point yaw. */
  yaw: number
}

/** A staged hazard. */
export type StagedHazard = {
  ids: string[]
  /** Text for `chooseRouteHazard`. */
  description: string
}

const SITE_POINT: Record<HazardSite, string> = { finishing_corner: 'finishing' }

/** Preset layouts: lorry, 2 boxes, sign (as a cone), 3 cones and a worker. */
export const HAZARD_PRESETS: Record<HazardPreset, { props: HazardProp[]; description: string }> = {
  forklift_unloading: {
    props: [
      { id: 'hz-truck-1', kind: 'truck', forward: 6, left: 3.5, yaw: 0 },
      { id: 'hz-box-1', kind: 'box', forward: 2, left: 1, yaw: 0 },
      { id: 'hz-box-2', kind: 'box', forward: 3.5, left: 0.5, yaw: 15 },
      { id: 'hz-sign-1', kind: 'cone', forward: -8, left: 1.5, yaw: 0 },
      { id: 'hz-cone-1', kind: 'cone', forward: -4, left: 0, yaw: 0 },
      { id: 'hz-cone-2', kind: 'cone', forward: -4, left: -1.5, yaw: 0 },
      { id: 'hz-cone-3', kind: 'cone', forward: -4, left: 1.5, yaw: 0 },
      { id: 'hz-worker-1', kind: 'worker', forward: 1, left: -0.5, yaw: 90 },
    ],
    description: 'Forklift unloading a lorry: 2 boxes, sign, 3 cones and 1 worker in the lane',
  },
}

/**
 * Spawns a hazard preset at a site.
 * @param ds - Driving system.
 * @param at - Hazard site.
 * @param preset - Preset name.
 * @returns Spawned prop ids and a description for the route choice.
 */
export function stageHazard(ds: DrivingSystem, at: HazardSite, preset: HazardPreset): StagedHazard {
  const origin = ds.getTransform(SITE_POINT[at])
  const yaw = (origin.yaw * Math.PI) / 180
  const c = Math.cos(yaw)
  const s = Math.sin(yaw)
  const { props, description } = HAZARD_PRESETS[preset]
  for (const prop of props) {
    const x = origin.x + prop.forward * c - prop.left * s
    const y = origin.y + prop.forward * s + prop.left * c
    ds.spawnProp(prop.id, prop.kind, x, y, origin.yaw + prop.yaw)
  }
  return { ids: props.map((prop) => prop.id), description }
}

/**
 * Removes staged hazard props.
 * @param ds - Driving system.
 * @param ids - Prop ids from {@link stageHazard}.
 */
export function clearHazard(ds: DrivingSystem, ids: readonly string[]): void {
  for (const id of ids) ds.removeProp(id)
}
