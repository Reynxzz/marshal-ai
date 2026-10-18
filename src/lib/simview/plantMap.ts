// Plant layout for the dashboard 3D view, built from the TEAM_PLAN named points (CARLA frame).
// Pure data: no three.js. Roads are straight strips between the points the scripted engine
// drives between (it moves in straight lines from waypoint to waypoint).

import { type NamedPoint, POINTS, YARD_SLOTS } from '#/lib/marshal/points'

/** A road segment between two named points. */
export type RoadSegment = {
  from: string
  to: string
  /** Start, CARLA frame metres. */
  ax: number
  ay: number
  /** End, CARLA frame metres. */
  bx: number
  by: number
  /** Centre of the strip. */
  cx: number
  cy: number
  length: number
  /** Heading from `from` to `to`, degrees (CARLA yaw). */
  yaw: number
}

/** What a marker stands for (drives its 3D shape). */
export type MarkerKind =
  | 'line'
  | 'buffer'
  | 'gate'
  | 'track'
  | 'report'
  | 'route'
  | 'rework'
  | 'yard_entry'
  | 'slot'
  | 'dock'

/** A labelled plant marker. */
export type PlantMarker = {
  /** Named point (or 'dock'). */
  name: string
  label: string
  kind: MarkerKind
  x: number
  y: number
  yaw: number
}

/** Axis-aligned bounds of the plant, CARLA frame. */
export type PlantBounds = { minX: number; maxX: number; minY: number; maxY: number }

/** Everything the 3D scene draws for the plant. */
export type PlantLayout = {
  roads: RoadSegment[]
  markers: PlantMarker[]
  bounds: PlantBounds
  center: { x: number; y: number }
}

/**
 * Point-to-point legs the flows send cars along (orchestrator, gate, lamp, route, parking).
 * Yard slots are reached from `yard.entry` by the parking manoeuvre.
 */
export const PLANT_SEGMENTS: readonly (readonly [string, string])[] = [
  ['line_end', 'buffer_zone_b'],
  ['buffer_zone_b', 'inspection_gate'],
  ['inspection_gate', 'test_track'],
  ['test_track', 'report_point'],
  ['inspection_gate', 'rework.electrical'],
  ['test_track', 'rework.electrical'],
  ['report_point', 'finishing'],
  ['finishing', 'yard.entry'],
  ['report_point', 'lane_c'],
  ['lane_c', 'yard.entry'],
  ...YARD_SLOTS.map((slot) => ['yard.entry', slot] as const),
]

const MARKER_INFO: Record<string, { label: string; kind: MarkerKind }> = {
  line_end: { label: 'Line end', kind: 'line' },
  buffer_zone_b: { label: 'Buffer B', kind: 'buffer' },
  inspection_gate: { label: 'Inspection gate', kind: 'gate' },
  test_track: { label: 'Test track', kind: 'track' },
  report_point: { label: 'Report point', kind: 'report' },
  lane_c: { label: 'Lane C', kind: 'route' },
  finishing: { label: 'Finishing corner', kind: 'route' },
  'rework.electrical': { label: 'Rework bay', kind: 'rework' },
  'yard.entry': { label: 'Yard entry', kind: 'yard_entry' },
}

/** Loading dock: not a named point, drawn beside the yard row (illustrative only). */
export const DOCK_POSE: NamedPoint = { x: -46, y: -48.75, yaw: 90 }

/**
 * Converts a CARLA-frame pose to three.js (TEAM_PLAN: position.set(x, z, y),
 * rotation.y = -degToRad(yaw)).
 * @param x - CARLA x.
 * @param y - CARLA y.
 * @param yaw - CARLA yaw, degrees.
 * @param height - three.js y (up).
 * @returns three.js position and rotation.y.
 */
export function toThree(
  x: number,
  y: number,
  yaw = 0,
  height = 0,
): { position: [number, number, number]; rotationY: number } {
  return { position: [x, height, y], rotationY: -(yaw * Math.PI) / 180 }
}

function segment(points: Record<string, NamedPoint>, from: string, to: string): RoadSegment | null {
  const a = points[from]
  const b = points[to]
  if (!a || !b) return null
  const dx = b.x - a.x
  const dy = b.y - a.y
  return {
    from,
    to,
    ax: a.x,
    ay: a.y,
    bx: b.x,
    by: b.y,
    cx: (a.x + b.x) / 2,
    cy: (a.y + b.y) / 2,
    length: Math.hypot(dx, dy),
    yaw: (Math.atan2(dy, dx) * 180) / Math.PI,
  }
}

/**
 * Builds the plant layout from named points.
 * @param points - Named points (defaults to TEAM_PLAN's table).
 * @param segments - Road legs (defaults to {@link PLANT_SEGMENTS}).
 * @returns Roads, labelled markers, bounds and centre.
 */
export function buildPlantLayout(
  points: Record<string, NamedPoint> = POINTS,
  segments: readonly (readonly [string, string])[] = PLANT_SEGMENTS,
): PlantLayout {
  const roads: RoadSegment[] = []
  for (const [from, to] of segments) {
    const road = segment(points, from, to)
    if (road && road.length > 0) roads.push(road)
  }
  const markers: PlantMarker[] = []
  for (const [name, pose] of Object.entries(points)) {
    const slot = /^yard\.(Y\d+)$/.exec(name)
    const info = slot ? { label: slot[1], kind: 'slot' as const } : MARKER_INFO[name]
    if (!info) continue
    markers.push({ name, label: info.label, kind: info.kind, x: pose.x, y: pose.y, yaw: pose.yaw })
  }
  markers.push({ name: 'dock', label: 'Dock', kind: 'dock', ...DOCK_POSE })
  const xs = markers.map((marker) => marker.x)
  const ys = markers.map((marker) => marker.y)
  const bounds = {
    minX: Math.min(...xs),
    maxX: Math.max(...xs),
    minY: Math.min(...ys),
    maxY: Math.max(...ys),
  }
  return {
    roads,
    markers,
    bounds,
    center: { x: (bounds.minX + bounds.maxX) / 2, y: (bounds.minY + bounds.maxY) / 2 },
  }
}
