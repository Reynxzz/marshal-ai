// Engine-side plant map (TEAM_PLAN M1.4): the canonical named points and the lane graph the
// flows drive, plus graph validation. Pure data: no three.js. Coordinates come from the Marshal
// contract (src/lib/marshal/points.ts), so they cannot drift from the TEAM_PLAN table.

import { type NamedPoint, POINTS, type PointName, YARD_SLOTS } from '#/lib/marshal/points'

/** One lane leg between two named points. */
export type LaneSegment = readonly [PointName, PointName]

/**
 * Legs the flows send cars along: the line corridor, both routes around the report point, the
 * rework return and the yard row. Matches the legs the dashboard viewer draws (plus the rework
 * return from `test_track` the lamp flow needs).
 */
export const LANE_SEGMENTS: readonly LaneSegment[] = [
  ['line_end', 'buffer_zone_b'],
  ['buffer_zone_b', 'inspection_gate'],
  ['inspection_gate', 'test_track'],
  ['test_track', 'report_point'],
  ['inspection_gate', 'rework.electrical'],
  ['test_track', 'rework.electrical'],
  ['rework.electrical', 'test_track'],
  ['report_point', 'finishing'],
  ['finishing', 'yard.entry'],
  ['report_point', 'lane_c'],
  ['lane_c', 'yard.entry'],
  ...YARD_SLOTS.map((slot) => ['yard.entry', slot] as const),
]

/** Loading dock pose (illustrative, not a named point in the TEAM_PLAN table). */
export const LOADING_DOCK_POSE: NamedPoint = { x: -46, y: -48.75, yaw: 90 }

/** One undirected lane edge with its straight-line length in metres. */
export type LaneEdge = { from: PointName; to: PointName; length: number }

/** Axis-aligned bounds of the named points, CARLA frame. */
export type PlantBounds = { minX: number; maxX: number; minY: number; maxY: number }

/** The lane graph the engine routes and samples lane centre-lines from. */
export type LaneGraph = {
  points: Record<PointName, NamedPoint>
  edges: LaneEdge[]
  /** Neighbour names of a point, both directions. */
  neighbors(name: PointName): PointName[]
  /** Straight-line distance between two named points, metres. */
  distance(from: PointName, to: PointName): number
  /**
   * Shortest path of named points (deterministic Dijkstra: smallest distance, then name).
   * @param from - Start point.
   * @param to - End point.
   * @returns Point names from `from` to `to` inclusive.
   */
  route(from: PointName, to: PointName): PointName[]
  /** Graph problems: broken references, zero-length legs, unreachable points. */
  validate(): string[]
}

function lengthOf(a: NamedPoint, b: NamedPoint): number {
  return Math.hypot(b.x - a.x, b.y - a.y)
}

/**
 * Builds the lane graph.
 * @param points - Named points (defaults to the Marshal contract table).
 * @param segments - Lane legs (defaults to {@link LANE_SEGMENTS}).
 * @returns Graph with neighbour lookups, distances and validation.
 */
export function createLaneGraph(
  points: Record<PointName, NamedPoint> = POINTS,
  segments: readonly LaneSegment[] = LANE_SEGMENTS,
): LaneGraph {
  const edges: LaneEdge[] = []
  const adjacency = new Map<PointName, PointName[]>()
  const problems: string[] = []
  const link = (from: PointName, to: PointName): void => {
    const list = adjacency.get(from) ?? []
    list.push(to)
    adjacency.set(from, list)
  }
  for (const [from, to] of segments) {
    const a = points[from]
    const b = points[to]
    if (!a || !b) {
      problems.push(`lane leg references an unknown point: ${from} -> ${to}`)
      continue
    }
    const length = lengthOf(a, b)
    if (length <= 0) {
      problems.push(`lane leg has zero length: ${from} -> ${to}`)
      continue
    }
    edges.push({ from, to, length })
    link(from, to)
    link(to, from)
  }
  const pointsOf = (name: PointName): NamedPoint => {
    const point = points[name]
    if (!point) throw new Error(`unknown point: ${name}`)
    return point
  }
  const route = (from: PointName, to: PointName): PointName[] => {
    pointsOf(from)
    pointsOf(to)
    if (from === to) return [from]
    const dist = new Map<PointName, number>([[from, 0]])
    const prev = new Map<PointName, PointName>()
    const visited = new Set<PointName>()
    while (true) {
      let current: PointName | undefined
      let best = Number.POSITIVE_INFINITY
      for (const [name, value] of dist) {
        if (visited.has(name)) continue
        if (value < best || (value === best && current !== undefined && name < current)) {
          best = value
          current = name
        }
      }
      if (current === undefined || best === Number.POSITIVE_INFINITY || current === to) break
      visited.add(current)
      for (const next of adjacency.get(current) ?? []) {
        if (visited.has(next)) continue
        const candidate = best + lengthOf(points[current], points[next])
        if (candidate < (dist.get(next) ?? Number.POSITIVE_INFINITY)) {
          dist.set(next, candidate)
          prev.set(next, current)
        }
      }
    }
    if (!dist.has(to)) throw new Error(`no route from ${from} to ${to}`)
    const path: PointName[] = [to]
    let cursor = to
    while (cursor !== from) {
      const step = prev.get(cursor)
      if (!step) throw new Error(`no route from ${from} to ${to}`)
      path.unshift(step)
      cursor = step
    }
    return path
  }
  return {
    points,
    edges,
    neighbors: (name) => [...(adjacency.get(name) ?? [])],
    distance: (from, to) => lengthOf(pointsOf(from), pointsOf(to)),
    route,
    validate() {
      const seen = new Set<PointName>(['line_end'])
      const queue: PointName[] = ['line_end']
      while (queue.length > 0) {
        const current = queue.shift() as PointName
        for (const next of adjacency.get(current) ?? []) {
          if (seen.has(next)) continue
          seen.add(next)
          queue.push(next)
        }
      }
      const unreachable = (Object.keys(points) as PointName[]).filter((name) => !seen.has(name))
      const notes = [...problems]
      if (unreachable.length > 0) notes.push(`unreachable points: ${unreachable.join(', ')}`)
      return notes
    },
  }
}

/**
 * Axis-aligned bounds of a named-point table.
 * @param points - Named points (defaults to the Marshal contract table).
 * @returns Bounds in the CARLA frame.
 */
export function boundsOf(points: Record<PointName, NamedPoint> = POINTS): PlantBounds {
  const list = Object.values(points)
  return {
    minX: Math.min(...list.map((point) => point.x)),
    maxX: Math.max(...list.map((point) => point.x)),
    minY: Math.min(...list.map((point) => point.y)),
    maxY: Math.max(...list.map((point) => point.y)),
  }
}
