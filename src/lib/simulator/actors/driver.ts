// Driving model (TEAM_PLAN M2.2-M2.4): pure-pursuit steering on a sampled lane centre-line, a
// smooth speed controller and the arrival rules. Pure math in the CARLA frame, yaw radians; the
// engine steps it at a fixed 0.1 s. No three.js, no React.

import type { ControlStep } from '#/lib/marshal/types'
import { CAR_WHEELBASE_M } from './car'

/** Control step, seconds (TEAM_PLAN: fixed-step 0.1 s). */
export const FIXED_STEP_S = 0.1
/** Lookahead distance, metres (TEAM_PLAN M2.2: ~2x car length). */
export const LOOKAHEAD_M = 9.8
/** Maximum front-wheel angle, radians (TEAM_PLAN: 30 degrees). */
export const MAX_STEER_RAD = (30 * Math.PI) / 180
/** Comfortable acceleration / braking, m/s^2. */
export const MAX_ACCEL_MPS2 = 1.2
export const BRAKE_MPS2 = 2.4
/** Normal waypoint arrival radius, metres (TEAM_PLAN M2.4: 3-4 m). */
export const ARRIVAL_RADIUS_M = 3.5
/** Pull-over approach: square up after this long if the lane never clears. */
export const PRECISE_TIMEOUT_S = 30
/** Gate approach: slow inside the zone and record the speed within the record radius. */
export const GATE_SLOW_ZONE_M = 15
export const GATE_SPEED_MPS = 5 / 3.6
export const GATE_RECORD_RADIUS_M = 4
/** Predictive safety stop (TEAM_PLAN M7.3): probe forward this far, halt under this clearance. */
export const SAFETY_PROBE_M = 0.4
export const SAFETY_CLEARANCE_M = 0.2

/** A point on a sampled lane centre-line, CARLA frame. */
export type PathPoint = { x: number; y: number }

/** Car motion state: position, heading (radians) and forward speed. */
export type VehicleState = { x: number; y: number; yaw: number; speed_mps: number }

function normalizeAngle(angle: number): number {
  return Math.atan2(Math.sin(angle), Math.cos(angle))
}

/**
 * Index of the closest path point at or after `fromIndex - 10`, so the driver never jumps
 * backwards along its own path.
 * @param path - Sampled centre-line.
 * @param x - Car x, CARLA frame.
 * @param y - Car y, CARLA frame.
 * @param fromIndex - Last known index (search starts a little before it).
 * @returns Closest point index (0 for an empty path).
 */
export function nearestIndex(
  path: readonly PathPoint[],
  x: number,
  y: number,
  fromIndex = 0,
): number {
  if (path.length === 0) return 0
  let best = Math.min(Math.max(0, fromIndex), path.length - 1)
  let bestDistance = Number.POSITIVE_INFINITY
  for (let index = Math.max(0, fromIndex - 10); index < path.length; index++) {
    const distance = (path[index].x - x) ** 2 + (path[index].y - y) ** 2
    if (distance < bestDistance) {
      bestDistance = distance
      best = index
    }
  }
  return best
}

/**
 * Point `distanceM` ahead along the path, clamped to its end.
 * @param path - Sampled centre-line.
 * @param index - Index to start from.
 * @param distanceM - Lookahead distance, metres.
 * @returns The lookahead point.
 */
export function pointAhead(
  path: readonly PathPoint[],
  index: number,
  distanceM: number,
): PathPoint {
  let remaining = distanceM
  for (let i = Math.max(0, index); i < path.length - 1; i++) {
    const from = path[i]
    const to = path[i + 1]
    const segment = Math.hypot(to.x - from.x, to.y - from.y)
    if (segment <= 1e-9) continue
    if (remaining <= segment) {
      const t = remaining / segment
      return { x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t }
    }
    remaining -= segment
  }
  return path[path.length - 1]
}

/**
 * Distance from a pose to the path end, following the polyline.
 * @param path - Sampled centre-line.
 * @param index - Closest point index.
 * @param x - Car x, CARLA frame.
 * @param y - Car y, CARLA frame.
 * @returns Remaining distance, metres.
 */
export function remainingDistance(
  path: readonly PathPoint[],
  index: number,
  x: number,
  y: number,
): number {
  let total = Math.hypot(path[index].x - x, path[index].y - y)
  for (let i = index; i < path.length - 1; i++) {
    total += Math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y)
  }
  return total
}

/**
 * Moves a speed towards a target within the comfort limits (TEAM_PLAN M2.3).
 * @param speed - Current speed, m/s.
 * @param target - Target speed, m/s.
 * @param dt - Step, seconds.
 * @returns New speed, m/s.
 */
export function approachSpeed(speed: number, target: number, dt: number): number {
  const delta = target - speed
  const limit = (delta > 0 ? MAX_ACCEL_MPS2 : BRAKE_MPS2) * dt
  return speed + Math.max(-limit, Math.min(limit, delta))
}

/**
 * Pure-pursuit front-wheel angle towards the lookahead point (TEAM_PLAN M2.2).
 * @param state - Car state.
 * @param lookahead - Lookahead point, CARLA frame.
 * @param lookaheadM - Lookahead distance used for the curvature.
 * @param wheelbase - Wheelbase, metres.
 * @returns Steering angle, radians, clamped to {@link MAX_STEER_RAD}.
 */
export function purePursuitSteer(
  state: VehicleState,
  lookahead: PathPoint,
  lookaheadM = LOOKAHEAD_M,
  wheelbase = CAR_WHEELBASE_M,
): number {
  const toLookahead = Math.atan2(lookahead.y - state.y, lookahead.x - state.x)
  const alpha = normalizeAngle(toLookahead - state.yaw)
  const steer = Math.atan2(2 * wheelbase * Math.sin(alpha), Math.max(1e-3, lookaheadM))
  return Math.max(-MAX_STEER_RAD, Math.min(MAX_STEER_RAD, steer))
}

/**
 * One fixed-step kinematic bicycle update.
 * @param state - Car state.
 * @param steer - Front-wheel angle, radians.
 * @param speed - Speed to move at, m/s.
 * @param dt - Step, seconds.
 * @returns Next car state.
 */
export function stepVehicle(
  state: VehicleState,
  steer: number,
  speed: number,
  dt = FIXED_STEP_S,
): VehicleState {
  const yaw = state.yaw + (speed / CAR_WHEELBASE_M) * Math.tan(steer) * dt
  return {
    x: state.x + speed * Math.cos(yaw) * dt,
    y: state.y + speed * Math.sin(yaw) * dt,
    yaw,
    speed_mps: speed,
  }
}

/**
 * Pose delta of one kinematic control step (TEAM_PLAN M7.1): exact arcs, gear R reverses,
 * `wheel` -1..1 maps to +/-30 degrees (positive = left).
 * @param state - Car state before the step.
 * @param step - Control step.
 * @returns Position and heading deltas in the CARLA frame.
 */
export function controlStepDelta(
  state: VehicleState,
  step: ControlStep,
): { dx: number; dy: number; dyaw: number } {
  const speed = step.speed_mps * (step.gear === 'R' ? -1 : 1)
  const travelled = speed * step.duration_s
  const wheel = Math.max(-1, Math.min(1, step.wheel)) * MAX_STEER_RAD
  const curvature = Math.tan(wheel) / CAR_WHEELBASE_M
  if (Math.abs(curvature) < 1e-9) {
    return { dx: travelled * Math.cos(state.yaw), dy: travelled * Math.sin(state.yaw), dyaw: 0 }
  }
  const next = state.yaw + travelled * curvature
  return {
    dx: (Math.sin(next) - Math.sin(state.yaw)) / curvature,
    dy: -(Math.cos(next) - Math.cos(state.yaw)) / curvature,
    dyaw: travelled * curvature,
  }
}
