// Flow 6 — Compile a validated spec into engine-ready setup: an ego start point, concrete
// incident actors placed with placeIncident-like geometry, and the declared metric names.

import type { IncidentActor } from '#/sim/incident'
import type { Scenario, ScenarioActor } from '#/sim/records'
import { buildPath, positionAt } from '#/sim/vehicle'
import type { World } from '#/sim/world'

export type ScenarioSetup = {
  spec: Scenario
  egoStart: string
  actors: IncidentActor[]
  metrics: string[]
}

function firstOutgoing(world: World, at: string): string {
  const seg = world.segments.find((s) => s.from === at)
  return seg ? seg.to : at
}

/** Actor offsetM metres along the first outgoing segment from its named point. */
function placeActor(world: World, actor: ScenarioActor, index: number): IncidentActor {
  const origin = world.points[actor.at]
  if (!origin) return { id: `actor.${index}`, kind: actor.kind, x: 0, y: 0 }
  const neighbor = firstOutgoing(world, actor.at)
  const path = buildPath(neighbor === actor.at ? [actor.at] : [actor.at, neighbor], world)
  const pos = positionAt(path, actor.offsetM)
  const lateral = actor.kind === 'walker' ? 2 : 0
  const nx = -Math.sin(pos.yaw)
  const ny = Math.cos(pos.yaw)
  return {
    id: `actor.${index}.${actor.kind}`,
    kind: actor.kind,
    x: pos.x + nx * lateral,
    y: pos.y + ny * lateral,
  }
}

export function compileSpec(spec: Scenario, world: World): ScenarioSetup {
  const inZone = Object.values(world.points).find((p) => p.zone === spec.zone)
  const egoStart = inZone ? inZone.name : 'line_end'
  return {
    spec,
    egoStart,
    actors: spec.actors.map((a, i) => placeActor(world, a, i)),
    metrics: spec.metrics,
  }
}
