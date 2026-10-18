// The plant's road network, as CARLA builds it (the backend's scripts/plant_xodr.py): a one-way
// loop of four straights (120 m, 60 m) and four 22 m-radius bends, two driving lanes and a parking
// shoulder on the outside, and lane C, a two-lane connector across the middle with 10 m turns at
// each end. Every named point sits on it exactly where CARLA puts it (POINTS): cars drive the outer
// lane, 5.25 m outside the reference line; bays and yard slots are on the shoulder, 8.75 m out.
//
// Each lane leg between two named points is the path a car really drives (curves, lane-to-shoulder
// pull-ins), sampled every metre, so the engine follows the roads the dashboard and the cameras
// draw. CARLA frame: x east, y as CARLA has it, headings in radians.

import type { PointName } from '#/lib/marshal/points'

export type Pt = { x: number; y: number }
export type Pose = Pt & { heading: number }

/** Lane offsets outside the reference line (CARLA lanes -1, -2, -3), metres. */
export const LANE = {
  inner: 1.75,
  outer: 5.25,
  shoulder: 8.75,
  /** Carriageway edge (two 3.5 m lanes). */
  edge: 7,
  /** Shoulder outer edge. */
  shoulderEdge: 10.5,
} as const

const STRAIGHT_LONG = 120
const STRAIGHT_SHORT = 60
const RADIUS = 22
const ARC = (Math.PI / 2) * RADIUS

type Piece =
  | { kind: 'line'; x: number; y: number; heading: number; length: number }
  | { kind: 'arc'; cx: number; cy: number; radius: number; start: number; length: number }

/**
 * A chain of lines and clockwise quarter arcs (the loop turns right in the CARLA frame as drawn
 * here, so "outside" is the left normal (-sin, cos) of the heading).
 */
function chain(x0: number, y0: number, heading0: number, parts: ['line' | 'arc', number][]) {
  const pieces: Piece[] = []
  let x = x0
  let y = y0
  let heading = heading0
  for (const [kind, size] of parts) {
    if (kind === 'line') {
      pieces.push({ kind, x, y, heading, length: size })
      x += size * Math.cos(heading)
      y += size * Math.sin(heading)
    } else {
      // Centre on the inside (right of the heading): (sin, -cos) * radius.
      const cx = x + size * Math.sin(heading)
      const cy = y - size * Math.cos(heading)
      pieces.push({ kind, cx, cy, radius: size, start: heading, length: (Math.PI / 2) * size })
      heading -= Math.PI / 2
      x = cx - size * Math.sin(heading)
      y = cy + size * Math.cos(heading)
    }
  }
  const total = pieces.reduce((sum, piece) => sum + piece.length, 0)
  const pose = (s: number): Pose => {
    let left = s
    for (const piece of pieces) {
      if (left <= piece.length + 1e-9 || piece === pieces[pieces.length - 1]) {
        const t = Math.min(left, piece.length)
        if (piece.kind === 'line') {
          return {
            x: piece.x + t * Math.cos(piece.heading),
            y: piece.y + t * Math.sin(piece.heading),
            heading: piece.heading,
          }
        }
        const heading = piece.start - t / piece.radius
        return {
          x: piece.cx - piece.radius * Math.sin(heading),
          y: piece.cy + piece.radius * Math.cos(heading),
          heading,
        }
      }
      left -= piece.length
    }
    throw new Error('unreachable')
  }
  return { pieces, total, pose }
}

/** The loop's reference line, from (0, 0) heading east. */
export const LOOP = chain(0, 0, 0, [
  ['line', STRAIGHT_LONG],
  ['arc', RADIUS],
  ['line', STRAIGHT_SHORT],
  ['arc', RADIUS],
  ['line', STRAIGHT_LONG],
  ['arc', RADIUS],
  ['line', STRAIGHT_SHORT],
  ['arc', RADIUS],
])

/** Lane C's reference line: leaves the loop at s = 102, turns across, joins the north straight. */
export const LANE_C_FROM = 102
const RADIUS_C = 10
export const LANE_C = chain(LANE_C_FROM, 0, 0, [
  ['arc', RADIUS_C],
  ['line', 104 - 2 * RADIUS_C],
  ['arc', RADIUS_C],
])

/** Where each straight starts on the loop. */
const S3 = STRAIGHT_LONG + ARC
const S5 = S3 + STRAIGHT_SHORT + ARC
const S7 = S5 + STRAIGHT_LONG + ARC
/** Where lane C rejoins the north straight (18 m along it). */
const S_LANE_C_JOIN = S5 + (STRAIGHT_LONG - LANE_C_FROM)

/** Loop position of each named point and its lateral offset (as plant_xodr.py places them). */
export const POINT_ON_LOOP: Partial<Record<PointName, { s: number; d: number }>> = {
  line_end: { s: 8, d: LANE.outer },
  buffer_zone_b: { s: 18, d: LANE.outer },
  inspection_gate: { s: 32, d: LANE.outer },
  test_track: { s: 62, d: LANE.outer },
  report_point: { s: 100, d: LANE.outer },
  'rework.electrical': { s: S3 + 18, d: LANE.shoulder },
  finishing: { s: S5 + 30, d: LANE.outer },
  'yard.entry': { s: S7 + 2, d: LANE.outer },
  ...Object.fromEntries(
    Array.from({ length: 8 }, (_, i) => [
      `yard.Y${i + 1}`,
      { s: S7 + 14 + 5.5 * i, d: LANE.shoulder },
    ]),
  ),
}

/** Lane C point: halfway along the connector, in its outer lane. */
const LANE_C_POINT_U = (Math.PI / 2) * RADIUS_C + 40

/** Position at distance s along a chain, d metres outside its reference line. */
export function at(path: { pose(s: number): Pose }, s: number, d: number): Pose {
  const pose = path.pose(s)
  return {
    x: pose.x - d * Math.sin(pose.heading),
    y: pose.y + d * Math.cos(pose.heading),
    heading: pose.heading,
  }
}

const wrap = (s: number): number => ((s % LOOP.total) + LOOP.total) % LOOP.total
const smooth = (t: number): number => {
  const k = Math.max(0, Math.min(1, t))
  return k * k * (3 - 2 * k)
}

/**
 * Samples a stretch of the loop every ~1 m, from s0 forward to s1 (wrapping past the start), the
 * lateral offset given per metre travelled.
 */
function along(
  s0: number,
  s1: number,
  offset: (travelled: number, length: number) => number,
): Pt[] {
  const length = s1 >= s0 ? s1 - s0 : s1 + LOOP.total - s0
  const steps = Math.max(1, Math.ceil(length))
  const out: Pt[] = []
  for (let i = 0; i <= steps; i++) {
    const travelled = (length * i) / steps
    const { x, y } = at(LOOP, wrap(s0 + travelled), offset(travelled, length))
    out.push({ x, y })
  }
  return out
}

/** Outer lane all the way, optionally easing onto the shoulder at the end / off it at the start. */
function loopLeg(from: { s: number; d: number }, to: { s: number; d: number }): Pt[] {
  const ease = 10
  return along(from.s, to.s, (travelled, length) => {
    let d: number = LANE.outer
    if (from.d !== LANE.outer) d += (from.d - LANE.outer) * (1 - smooth(travelled / ease))
    if (to.d !== LANE.outer) d += (to.d - LANE.outer) * smooth((travelled - (length - ease)) / ease)
    return d
  })
}

function laneCLeg(u0: number, u1: number): Pt[] {
  const steps = Math.max(1, Math.ceil(u1 - u0))
  return Array.from({ length: steps + 1 }, (_, i) => {
    const { x, y } = at(LANE_C, u0 + ((u1 - u0) * i) / steps, LANE.outer)
    return { x, y }
  })
}

const join = (...parts: Pt[][]): Pt[] =>
  parts.flatMap((part, i) => (i === 0 ? part : part.slice(1)))
const P = (name: PointName) => POINT_ON_LOOP[name] as { s: number; d: number }
const lane = (s: number) => ({ s, d: LANE.outer as number })

/** The directed lane legs between named points, each with the path a car drives. */
export const PLANT_LEGS: readonly { from: PointName; to: PointName; path: () => Pt[] }[] = [
  { from: 'line_end', to: 'buffer_zone_b', path: () => loopLeg(P('line_end'), P('buffer_zone_b')) },
  {
    from: 'buffer_zone_b',
    to: 'inspection_gate',
    path: () => loopLeg(P('buffer_zone_b'), P('inspection_gate')),
  },
  {
    from: 'inspection_gate',
    to: 'test_track',
    path: () => loopLeg(P('inspection_gate'), P('test_track')),
  },
  {
    from: 'test_track',
    to: 'report_point',
    path: () => loopLeg(P('test_track'), P('report_point')),
  },
  // The finishing corner: on past the rework bays, round the north-east bend.
  { from: 'report_point', to: 'finishing', path: () => loopLeg(P('report_point'), P('finishing')) },
  // Into a rework bay on the east straight's shoulder, and back out to finishing or round the
  // loop to the test track again (CARLA: "a reworked car can come round to the gate again").
  {
    from: 'report_point',
    to: 'rework.electrical',
    path: () => loopLeg(P('report_point'), P('rework.electrical')),
  },
  {
    from: 'rework.electrical',
    to: 'finishing',
    path: () => loopLeg(P('rework.electrical'), P('finishing')),
  },
  {
    from: 'rework.electrical',
    to: 'test_track',
    path: () => loopLeg(P('rework.electrical'), P('test_track')),
  },
  // Lane C: across the middle, skipping the finishing corner.
  {
    from: 'report_point',
    to: 'lane_c',
    path: () => join(loopLeg(P('report_point'), lane(LANE_C_FROM)), laneCLeg(0, LANE_C_POINT_U)),
  },
  {
    from: 'lane_c',
    to: 'finishing',
    path: () =>
      join(laneCLeg(LANE_C_POINT_U, LANE_C.total), loopLeg(lane(S_LANE_C_JOIN), P('finishing'))),
  },
  { from: 'finishing', to: 'yard.entry', path: () => loopLeg(P('finishing'), P('yard.entry')) },
  ...Array.from({ length: 8 }, (_, i) => {
    const slot = `yard.Y${i + 1}` as PointName
    return [
      { from: 'yard.entry' as PointName, to: slot, path: () => loopLeg(P('yard.entry'), P(slot)) },
      // Out of the slot, round the south-west bend to the line end (the only way on a one-way loop).
      { from: slot, to: 'line_end' as PointName, path: () => loopLeg(P(slot), P('line_end')) },
    ]
  }).flat(),
  { from: 'yard.entry', to: 'line_end', path: () => loopLeg(P('yard.entry'), P('line_end')) },
]

/** Length of a polyline, metres. */
export function pathLength(points: readonly Pt[]): number {
  let total = 0
  for (let i = 1; i < points.length; i++) {
    total += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y)
  }
  return total
}

/** The finishing corner's mid-point in the driving lane (where Scene A stages its hazard). */
export const FINISHING_CORNER_POSE: Pose = at(LOOP, S3 + STRAIGHT_SHORT + ARC / 2, LANE.outer)

/** Every road surface as a reference polyline with its paved band (for drawing). */
export type RoadBand = {
  name: string
  /** Reference line samples (every ~1 m). */
  line: Pose[]
  /** Paved from offset `from` to `to` outside the reference line. */
  from: number
  to: number
  closed: boolean
}

/** Samples a chain's reference line every ~1 m. */
export function sampleChain(path: { total: number; pose(s: number): Pose }, step = 1): Pose[] {
  const steps = Math.ceil(path.total / step)
  return Array.from({ length: steps + 1 }, (_, i) => path.pose((path.total * i) / steps))
}

/** The staging road from the assembly hall to the line end (spawned cars queue on it). */
export const STAGING = { fromX: -70, toX: 0, y: LANE.outer, width: 7 } as const
