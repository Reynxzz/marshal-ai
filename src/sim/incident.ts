// Incident placement for flow 2: blockers block a car's lane a few metres after the
// first junction on its route. Pure geometry, no rendering.

import type { IncidentBlocker } from './records'
import { buildPath, positionAt } from './vehicle'
import type { World } from './world'

export type IncidentActor = { id: string; kind: 'box' | 'walker' | 'vehicle'; x: number; y: number }

export type IncidentPlacement = {
  at: string
  blockDistanceM: number
  actors: IncidentActor[]
  situation: string
}

const BLOCKER_NOUNS: Record<IncidentBlocker, string> = {
  box: 'a maintenance box',
  walker: 'a worker',
  vehicle: 'a stopped forklift',
}

function describeBlockers(blockers: IncidentBlocker[]): string {
  const nouns = blockers.map((b) => BLOCKER_NOUNS[b])
  const list =
    nouns.length > 1 ? `${nouns.slice(0, -1).join(', ')} and ${nouns.at(-1)}` : (nouns[0] ?? '')
  return list.charAt(0).toUpperCase() + list.slice(1)
}

/** Returns null when the route is too short to place an incident. */
export function placeIncident(
  world: World,
  route: string[],
  blockers: IncidentBlocker[] = ['box', 'walker'],
): IncidentPlacement | null {
  if (route.length < 2) return null
  const path = buildPath(route, world)
  const legLen = path.cumulative[1] ?? 0
  if (legLen < 12) return null
  const composition: IncidentBlocker[] = blockers.length
    ? [...new Set(blockers)]
    : ['box', 'walker']
  const blockDistanceM = Math.max(5, legLen - 10)
  const pos = positionAt(path, blockDistanceM)
  const nx = -Math.sin(pos.yaw)
  const ny = Math.cos(pos.yaw)
  const vehiclePos = positionAt(path, Math.min(blockDistanceM + 4, Math.max(0, legLen - 1)))

  const actors: IncidentActor[] = []
  for (const kind of composition) {
    if (kind === 'box') actors.push({ id: 'incident.box', kind, x: pos.x, y: pos.y })
    else if (kind === 'walker')
      actors.push({ id: 'incident.walker', kind, x: pos.x + nx * 2, y: pos.y + ny * 2 })
    else actors.push({ id: 'incident.vehicle', kind, x: vehiclePos.x, y: vehiclePos.y })
  }

  return {
    at: route[1] as string,
    blockDistanceM,
    actors,
    situation: `${describeBlockers([...composition])} ${
      composition.length > 1 ? 'block' : 'blocks'
    } the lane ${Math.round(blockDistanceM)} m ahead.`,
  }
}
