// Camera goals for the dashboard 3D view (three.js frame, y up). Pure math, no three.js.

import type { Pose } from '#/lib/marshal/types'
import type { PlantLayout } from './plantMap'

export type Vec3 = [number, number, number]

/** Where the camera sits and what it looks at. */
export type CameraPose = { position: Vec3; target: Vec3 }

/** Follow camera: metres behind the car, above it, and to its side. */
export const FOLLOW = { back: 16, up: 10, side: 4 } as const

/**
 * Overview camera above the whole plant.
 * @param layout - Plant layout.
 * @returns Camera pose looking at the plant centre.
 */
export function overviewCamera(layout: PlantLayout): CameraPose {
  const { bounds, center } = layout
  const span = Math.max(bounds.maxX - bounds.minX, bounds.maxY - bounds.minY)
  return {
    position: [center.x, span * 0.62, center.y + span * 0.42],
    target: [center.x, 0, center.y],
  }
}

/**
 * Follow camera behind a car (CARLA pose, yaw in degrees).
 * @param pose - Car pose in the CARLA frame.
 * @returns Camera pose looking at the car.
 */
export function followCamera(pose: Pose): CameraPose {
  const yaw = (pose.yaw * Math.PI) / 180
  const c = Math.cos(yaw)
  const s = Math.sin(yaw)
  // CARLA (x, y) -> three (x, z); "left" of the heading is (-s, c).
  const x = pose.x - FOLLOW.back * c - FOLLOW.side * s
  const z = pose.y - FOLLOW.back * s + FOLLOW.side * c
  return { position: [x, FOLLOW.up, z], target: [pose.x, 1, pose.y] }
}

function lerp3(a: Vec3, b: Vec3, t: number): Vec3 {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
}

/**
 * Moves the camera a frame towards its goal (exponential smoothing, frame-rate independent).
 * @param current - Camera pose now.
 * @param goal - Camera goal.
 * @param dt - Real seconds since the last frame.
 * @param rate - Convergence rate per second (default 3).
 * @returns The next camera pose.
 */
export function stepCamera(
  current: CameraPose,
  goal: CameraPose,
  dt: number,
  rate = 3,
): CameraPose {
  const t = 1 - Math.exp(-rate * Math.max(0, Math.min(dt, 1)))
  return {
    position: lerp3(current.position, goal.position, t),
    target: lerp3(current.target, goal.target, t),
  }
}
