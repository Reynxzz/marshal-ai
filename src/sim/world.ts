// Plant map: top-down 2D points in metres. The viewer maps x -> three.x, y -> three.z.
// Pure data + routing, no three.js / React. Standalone copy (see tasks/README aturan #1).

export type Zone =
  | 'line'
  | 'inspection'
  | 'track'
  | 'report'
  | 'lane'
  | 'finishing'
  | 'rework'
  | 'yard'
  | 'buffer'

export type Point = { name: string; zone: Zone; x: number; y: number }
export type Segment = { from: string; to: string; oneWay?: boolean }

export type World = {
  points: Record<string, Point>
  segments: Segment[]
  point(name: string): Point
  /** Shortest path of point names from `from` to `to`. Throws if unreachable. */
  route(from: string, to: string, opts?: { avoid?: string[] }): string[]
  routeLength(points: Array<string | Point>): number
}

const POINT_LIST: Point[] = [
  { name: 'line_end', zone: 'line', x: 10, y: 40 },
  { name: 'inspection_gate', zone: 'inspection', x: 65, y: 40 },
  { name: 'test_track', zone: 'track', x: 125, y: 40 },
  { name: 'report_point', zone: 'report', x: 175, y: 40 },

  // Route A: report_point -> finishing corners -> finishing -> yard.entry
  { name: 'finishing_corner.p1', zone: 'finishing', x: 200, y: 40 },
  { name: 'finishing_corner.p2', zone: 'finishing', x: 222, y: 28 },
  { name: 'finishing_corner.p3', zone: 'finishing', x: 222, y: 12 },
  { name: 'finishing', zone: 'finishing', x: 200, y: 12 },
  { name: 'yard.entry', zone: 'yard', x: 180, y: 12 },

  // Route B: report_point -> lane_c -> finishing (longer detour)
  { name: 'lane_c', zone: 'lane', x: 120, y: 68 },

  // Yard slots + loading
  { name: 'yard.Y1', zone: 'yard', x: 158, y: 4 },
  { name: 'yard.Y2', zone: 'yard', x: 158, y: 10 },
  { name: 'yard.Y3', zone: 'yard', x: 158, y: 16 },
  { name: 'yard.Y4', zone: 'yard', x: 158, y: 22 },
  { name: 'yard.Y5', zone: 'yard', x: 143, y: 4 },
  { name: 'yard.Y6', zone: 'yard', x: 143, y: 10 },
  { name: 'yard.Y7', zone: 'yard', x: 143, y: 16 },
  { name: 'yard.Y8', zone: 'yard', x: 143, y: 22 },
  { name: 'loading_bay', zone: 'yard', x: 126, y: 12 },

  // Rework bays (top lane), one-way loop back to inspection_gate
  { name: 'rework.paint', zone: 'rework', x: 175, y: 72 },
  { name: 'rework.body', zone: 'rework', x: 160, y: 72 },
  { name: 'rework.electrical', zone: 'rework', x: 145, y: 72 },
  { name: 'rework.mechanical', zone: 'rework', x: 130, y: 72 },

  { name: 'buffer_zone_b', zone: 'buffer', x: 10, y: 72 },
]

const SEGMENT_LIST: Segment[] = [
  { from: 'line_end', to: 'inspection_gate' },
  { from: 'inspection_gate', to: 'test_track' },
  { from: 'test_track', to: 'report_point' },

  // Route A
  { from: 'report_point', to: 'finishing_corner.p1' },
  { from: 'finishing_corner.p1', to: 'finishing_corner.p2' },
  { from: 'finishing_corner.p2', to: 'finishing_corner.p3' },
  { from: 'finishing_corner.p3', to: 'finishing' },
  { from: 'finishing', to: 'yard.entry' },

  // Route B
  { from: 'report_point', to: 'lane_c' },
  { from: 'lane_c', to: 'finishing' },

  // Yard: entry to every slot + loading bay
  ...POINT_LIST.filter((p) => p.name.startsWith('yard.Y')).map((p) => ({
    from: 'yard.entry',
    to: p.name,
  })),
  { from: 'yard.entry', to: 'loading_bay' },
  { from: 'loading_bay', to: 'yard.entry' },

  // Rework: report -> first bay, bay chain, each bay back to the gate (one-way)
  { from: 'report_point', to: 'rework.paint', oneWay: true },
  { from: 'rework.paint', to: 'rework.body', oneWay: true },
  { from: 'rework.body', to: 'rework.electrical', oneWay: true },
  { from: 'rework.electrical', to: 'rework.mechanical', oneWay: true },
  { from: 'rework.paint', to: 'inspection_gate', oneWay: true },
  { from: 'rework.body', to: 'inspection_gate', oneWay: true },
  { from: 'rework.electrical', to: 'inspection_gate', oneWay: true },
  { from: 'rework.mechanical', to: 'inspection_gate', oneWay: true },

  { from: 'line_end', to: 'buffer_zone_b' },
]

function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

export function createWorld(): World {
  const points: Record<string, Point> = {}
  for (const p of POINT_LIST) points[p.name] = p

  const adjacency = new Map<string, Array<{ to: string; w: number }>>()
  const addEdge = (from: string, to: string) => {
    if (!points[from] || !points[to]) {
      throw new Error(`segment references unknown point: ${from} -> ${to}`)
    }
    const list = adjacency.get(from) ?? []
    list.push({ to, w: distance(points[from], points[to]) })
    adjacency.set(from, list)
  }
  for (const s of SEGMENT_LIST) {
    addEdge(s.from, s.to)
    if (!s.oneWay) addEdge(s.to, s.from)
  }

  const point = (name: string): Point => {
    const p = points[name]
    if (!p) throw new Error(`unknown point: ${name}`)
    return p
  }

  const route = (from: string, to: string, opts?: { avoid?: string[] }): string[] => {
    if (!points[from]) throw new Error(`unknown point: ${from}`)
    if (!points[to]) throw new Error(`unknown point: ${to}`)
    const avoidZones = new Set(opts?.avoid ?? [])
    if (from === to) return [from]

    const dist = new Map<string, number>([[from, 0]])
    const prev = new Map<string, string>()
    const visited = new Set<string>()

    while (true) {
      // Deterministic pick: smallest distance, then lexicographically smallest name.
      let current: string | undefined
      let best = Number.POSITIVE_INFINITY
      for (const [name, d] of dist) {
        if (visited.has(name)) continue
        if (d < best || (d === best && current !== undefined && name < current)) {
          best = d
          current = name
        }
      }
      if (current === undefined || best === Number.POSITIVE_INFINITY) break
      if (current === to) break
      visited.add(current)

      for (const edge of adjacency.get(current) ?? []) {
        const candidate = points[edge.to]
        if (edge.to !== to && avoidZones.has(candidate.zone)) continue
        if (visited.has(edge.to)) continue
        const nd = best + edge.w
        if (nd < (dist.get(edge.to) ?? Number.POSITIVE_INFINITY)) {
          dist.set(edge.to, nd)
          prev.set(edge.to, current)
        }
      }
    }

    if (!dist.has(to)) throw new Error(`no route from ${from} to ${to}`)
    const path: string[] = [to]
    let cursor = to
    while (cursor !== from) {
      const step = prev.get(cursor)
      if (!step) throw new Error(`no route from ${from} to ${to}`)
      path.unshift(step)
      cursor = step
    }
    return path
  }

  const routeLength = (list: Array<string | Point>): number => {
    let total = 0
    for (let i = 1; i < list.length; i++) {
      const prev = list[i - 1]
      const cur = list[i]
      const a = typeof prev === 'string' ? point(prev) : prev
      const b = typeof cur === 'string' ? point(cur) : cur
      total += distance(a, b)
    }
    return total
  }

  return { points, segments: SEGMENT_LIST, point, route, routeLength }
}

export const world = createWorld()

/** Zones reachable in the yard by a truck, used by flow 1. */
export const YARD_SLOTS = POINT_LIST.filter((p) => p.name.startsWith('yard.Y')).map((p) => p.name)
