import { describe, expect, it } from 'vitest'
import {
  approachSpeed,
  BRAKE_MPS2,
  controlStepDelta,
  MAX_ACCEL_MPS2,
  MAX_STEER_RAD,
  nearestIndex,
  pointAhead,
  purePursuitSteer,
  stepVehicle,
} from '../actors/driver'

const straight = Array.from({ length: 21 }, (_, index) => ({ x: index, y: 0 }))

describe('driver', () => {
  it('finds the closest path point', () => {
    expect(nearestIndex(straight, 5.4, 0.2)).toBe(5)
    expect(nearestIndex(straight, 0.1, 0.1)).toBe(0)
    expect(nearestIndex(straight, 100, 10)).toBe(20)
  })

  it('looks ahead along the path and clamps at its end', () => {
    expect(pointAhead(straight, 0, 9.8)).toEqual({ x: 9.8, y: 0 })
    expect(pointAhead(straight, 5, 100)).toEqual({ x: 20, y: 0 })
  })

  it('steers straight ahead and clamps hard turns to 30 degrees', () => {
    const state = { x: 0, y: 0, yaw: 0, speed_mps: 0 }
    expect(purePursuitSteer(state, { x: 10, y: 0 })).toBeCloseTo(0)
    expect(purePursuitSteer(state, { x: 0, y: -5 })).toBeCloseTo(-MAX_STEER_RAD)
    expect(purePursuitSteer(state, { x: 0, y: 5 })).toBeCloseTo(MAX_STEER_RAD)
  })

  it('moves the speed within the accel and brake limits', () => {
    expect(approachSpeed(0, 10, 0.1)).toBeCloseTo(MAX_ACCEL_MPS2 * 0.1)
    expect(approachSpeed(5, 0, 0.1)).toBeCloseTo(5 - BRAKE_MPS2 * 0.1)
    expect(approachSpeed(2, 2.01, 0.5)).toBeCloseTo(2.01)
  })

  it('turns the heading with the kinematic bicycle model', () => {
    const next = stepVehicle({ x: 0, y: 0, yaw: 0, speed_mps: 2 }, MAX_STEER_RAD, 2, 0.1)
    expect(next.yaw).toBeGreaterThan(0)
    expect(next.x).toBeGreaterThan(0)
    expect(next.y).toBeGreaterThan(0)
    expect(next.speed_mps).toBe(2)
  })

  it('integrates control steps: straight, arcs and reverse (M7.1)', () => {
    const state = { x: 0, y: 0, yaw: 0, speed_mps: 0 }
    const straight = controlStepDelta(state, { speed_mps: 1, wheel: 0, duration_s: 2, gear: 'D' })
    expect(straight.dx).toBeCloseTo(2)
    expect(straight.dy).toBeCloseTo(0)
    expect(straight.dyaw).toBe(0)

    const left = controlStepDelta(state, { speed_mps: 1, wheel: 1, duration_s: 2, gear: 'D' })
    expect(left.dyaw).toBeGreaterThan(0)
    expect(left.dx).toBeGreaterThan(0)
    expect(left.dy).toBeGreaterThan(0)

    const reverse = controlStepDelta(state, { speed_mps: 1, wheel: 0, duration_s: 1, gear: 'R' })
    expect(reverse.dx).toBeCloseTo(-1)
  })

  it('clamps the steering input to full lock', () => {
    const state = { x: 0, y: 0, yaw: 0, speed_mps: 0 }
    const clamped = controlStepDelta(state, { speed_mps: 1, wheel: 50, duration_s: 1, gear: 'D' })
    const full = controlStepDelta(state, { speed_mps: 1, wheel: 1, duration_s: 1, gear: 'D' })
    expect(clamped.dyaw).toBeCloseTo(full.dyaw)
  })
})
