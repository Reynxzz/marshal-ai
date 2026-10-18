// Footprint geometry for the scripted engine's traffic rules and for overlap tests:
// oriented-rectangle (SAT) intersection and a "too close in the same lane" check. Cars are
// 4.9 m x 1.9 m (TEAM_PLAN).

import type { Pose } from '../types'

/** Car footprint length (m). */
export const CAR_LENGTH_M = 4.9

/** Car footprint width (m). */
export const CAR_WIDTH_M = 1.9

/** An oriented rectangle: centre, heading (degrees), length along the heading and width. */
export type Rect = { x: number; y: number; yaw: number; length: number; width: number }

const DEG = Math.PI / 180

function corners(rect: Rect): [number, number][] {
  const c = Math.cos(rect.yaw * DEG)
  const s = Math.sin(rect.yaw * DEG)
  const hl = rect.length / 2
  const hw = rect.width / 2
  return [
    [hl, hw],
    [hl, -hw],
    [-hl, -hw],
    [-hl, hw],
  ].map(([f, l]) => [rect.x + f * c - l * s, rect.y + f * s + l * c])
}

/**
 * Whether two oriented rectangles intersect (separating-axis test; touching is not overlap).
 * @param a - First rectangle.
 * @param b - Second rectangle.
 * @returns True when they overlap.
 */
export function rectsOverlap(a: Rect, b: Rect): boolean {
  const reach = Math.hypot(a.length, a.width) / 2 + Math.hypot(b.length, b.width) / 2
  if (Math.hypot(a.x - b.x, a.y - b.y) >= reach) return false
  const pa = corners(a)
  const pb = corners(b)
  for (const yaw of [a.yaw, a.yaw + 90, b.yaw, b.yaw + 90]) {
    const ax = Math.cos(yaw * DEG)
    const ay = Math.sin(yaw * DEG)
    const ra = pa.map(([x, y]) => x * ax + y * ay)
    const rb = pb.map(([x, y]) => x * ax + y * ay)
    if (Math.max(...ra) <= Math.min(...rb) || Math.max(...rb) <= Math.min(...ra)) return false
  }
  return true
}

/**
 * A car footprint.
 * @param pose - Car pose (yaw in degrees).
 * @param margin_m - Grows (positive) or shrinks (negative) every side.
 * @returns The footprint rectangle.
 */
export function carRect(pose: Pose, margin_m = 0): Rect {
  return {
    x: pose.x,
    y: pose.y,
    yaw: pose.yaw,
    length: CAR_LENGTH_M + 2 * margin_m,
    width: CAR_WIDTH_M + 2 * margin_m,
  }
}

/**
 * Whether two car footprints intersect.
 * @param a - First car pose.
 * @param b - Second car pose.
 * @param tolerance_m - Footprints are shrunk by this much on every side first.
 * @returns True when the (shrunk) footprints overlap.
 */
export function carsOverlap(a: Pose, b: Pose, tolerance_m = 0): boolean {
  return rectsOverlap(carRect(a, -tolerance_m), carRect(b, -tolerance_m))
}

/**
 * Smallest angle between two headings.
 * @param a - Degrees.
 * @param b - Degrees.
 * @returns 0..180 degrees.
 */
export function headingDiff(a: number, b: number): number {
  return Math.abs(((((a - b) % 360) + 540) % 360) - 180)
}

/**
 * Whether two cars are in the same lane (roughly parallel, side by side within a car width)
 * with their centres closer than `gap_m`.
 * @param a - First car pose.
 * @param b - Second car pose.
 * @param gap_m - Centre distance below which they count as too close (default 5 m).
 * @returns True when too close in the same lane.
 */
export function tooCloseInLane(a: Pose, b: Pose, gap_m = 5): boolean {
  const dx = b.x - a.x
  const dy = b.y - a.y
  if (Math.hypot(dx, dy) >= gap_m) return false
  const diff = headingDiff(a.yaw, b.yaw)
  if (diff > 20 && diff < 160) return false
  const lateral = Math.abs(-dx * Math.sin(a.yaw * DEG) + dy * Math.cos(a.yaw * DEG))
  return lateral < CAR_WIDTH_M
}
