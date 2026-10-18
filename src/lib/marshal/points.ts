// Named plant map points (TEAM_PLAN "Plant Map Points Reference"), CARLA frame, yaw in degrees.
// Values must match the table exactly.

/** A named map point. */
export type NamedPoint = { x: number; y: number; yaw: number }

export const POINTS = {
  line_end: { x: 8.0, y: 5.25, yaw: 0 },
  buffer_zone_b: { x: 18.0, y: 5.25, yaw: 0 },
  inspection_gate: { x: 32.0, y: 5.25, yaw: 0 },
  test_track: { x: 62.0, y: 5.25, yaw: 0 },
  report_point: { x: 100.0, y: 5.25, yaw: 0 },
  lane_c: { x: 117.25, y: -50.0, yaw: -90 },
  finishing: { x: 90.0, y: -109.25, yaw: 180 },
  'rework.electrical': { x: 150.75, y: -40.0, yaw: -90 },
  'yard.entry': { x: -27.25, y: -80.0, yaw: 90 },
  'yard.Y1': { x: -30.75, y: -68.0, yaw: 90 },
  'yard.Y2': { x: -30.75, y: -62.5, yaw: 90 },
  'yard.Y3': { x: -30.75, y: -57.0, yaw: 90 },
  'yard.Y4': { x: -30.75, y: -51.5, yaw: 90 },
  'yard.Y5': { x: -30.75, y: -46.0, yaw: 90 },
  'yard.Y6': { x: -30.75, y: -40.5, yaw: 90 },
  'yard.Y7': { x: -30.75, y: -35.0, yaw: 90 },
  'yard.Y8': { x: -30.75, y: -29.5, yaw: 90 },
} as const satisfies Record<string, NamedPoint>

/** Every named point. */
export type PointName = keyof typeof POINTS

/** The eight yard pull-over slots, Y1..Y8. */
export const YARD_SLOTS = [
  'yard.Y1',
  'yard.Y2',
  'yard.Y3',
  'yard.Y4',
  'yard.Y5',
  'yard.Y6',
  'yard.Y7',
  'yard.Y8',
] as const satisfies readonly PointName[]

/**
 * Type guard for named points.
 * @param name - Candidate point name.
 * @returns True when `name` is a known named point.
 */
export function isPointName(name: string): name is PointName {
  return Object.hasOwn(POINTS, name)
}
