import type { CarSnapshot } from '#/sim/vehicle'

export type Pose = { x: number; y: number; yaw: number }

function shortestAngle(delta: number): number {
  return Math.atan2(Math.sin(delta), Math.cos(delta))
}

/**
 * Linear position + shortest-arc yaw interpolation between two snapshots.
 * `prev` is optional so the first frame can snap straight to `next`.
 */
export function interpolatePose(
  prev: CarSnapshot | undefined,
  next: CarSnapshot,
  alpha: number,
): Pose {
  if (!prev) return { x: next.x, y: next.y, yaw: next.yaw }

  const delta = shortestAngle(next.yaw - prev.yaw)
  return {
    x: prev.x + (next.x - prev.x) * alpha,
    y: prev.y + (next.y - prev.y) * alpha,
    yaw: shortestAngle(prev.yaw + delta * alpha),
  }
}
