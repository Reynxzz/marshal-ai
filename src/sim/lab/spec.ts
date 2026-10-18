// Flow 6 — ScenarioSpec: the zod contract re-exported for the lab, plus a code-only
// validator. The generator never writes code, only data that must pass here.

import type { Scenario } from '#/sim/records'
import type { World } from '#/sim/world'

export { scenarioSchema as ScenarioSpec } from '#/sim/records'
export type ScenarioSpec = Scenario

const KINDS = new Set(['walker', 'box', 'vehicle'])
export const MAX_OFFSET_M = 10

export function validateSpec(spec: Scenario, world: World): { ok: boolean; errors: string[] } {
  const errors: string[] = []

  const zoneExists = Object.values(world.points).some((p) => p.zone === spec.zone)
  if (!zoneExists) errors.push(`unknown zone: ${spec.zone}`)

  for (const actor of spec.actors) {
    if (!(actor.at in world.points)) errors.push(`unknown point: ${actor.at}`)
    if (!(actor.offsetM >= 0 && actor.offsetM <= MAX_OFFSET_M)) {
      errors.push(`offset out of range 0..${MAX_OFFSET_M}: ${actor.offsetM}`)
    }
    if (!KINDS.has(actor.kind)) errors.push(`unknown actor kind: ${actor.kind}`)
  }

  return { ok: errors.length === 0, errors }
}
