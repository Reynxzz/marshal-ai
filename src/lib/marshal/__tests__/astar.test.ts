import { describe, expect, it } from 'vitest'
import { CAR_GEOMETRY, isParked, planCandidatePaths } from '../parking/astar'
import { POINTS } from '../points'
import type { ObstacleBox, ParkingSlot, Pose } from '../types'

const slot: ParkingSlot = { id: 'yard.Y7', ...POINTS['yard.Y7'] }
// 3m behind the slot along its heading (+y), 1m off to the side, slightly rotated.
const start: Pose = { x: slot.x + 1, y: slot.y - 3, yaw: 95 }

describe('isParked', () => {
  it('accepts poses within 0.3m lateral and 8 deg heading', () => {
    expect(isParked({ x: slot.x, y: slot.y, yaw: 90 }, slot)).toBe(true)
    expect(isParked({ x: slot.x + 0.29, y: slot.y, yaw: 97.9 }, slot)).toBe(true)
  })

  it('rejects poses outside the tolerances', () => {
    expect(isParked({ x: slot.x + 0.31, y: slot.y, yaw: 90 }, slot)).toBe(false)
    expect(isParked({ x: slot.x, y: slot.y, yaw: 90 + 8.5 }, slot)).toBe(false)
    expect(isParked({ x: slot.x, y: slot.y - 2, yaw: 90 }, slot)).toBe(false)
  })
})

describe('planCandidatePaths', () => {
  it('uses the TEAM_PLAN car geometry', () => {
    expect(CAR_GEOMETRY).toMatchObject({
      length_m: 4.9,
      width_m: 1.9,
      wheelbase_m: 2.85,
      maxSteerDeg: 30,
      sideClearance_m: 0.3,
      headingToleranceDeg: 8,
    })
  })

  it('finds a path into an empty slot that ends parked', () => {
    const started = performance.now()
    const paths = planCandidatePaths(start, [], slot)
    const elapsed = performance.now() - started
    expect(paths.length).toBeGreaterThan(0)
    expect(paths[0].label).toBe('A')
    expect(paths[0].steps.length).toBeGreaterThan(0)
    expect(isParked(paths[0].end, slot)).toBe(true)
    for (const step of paths[0].steps) {
      expect(Math.abs(step.wheel)).toBeLessThanOrEqual(1)
      expect(step.duration_s).toBeGreaterThan(0)
    }
    // Generous bound for CI noise; the plan target is <50ms.
    expect(elapsed).toBeLessThan(1000)
  })

  it('returns at most 3 distinct candidates sorted by cost and is deterministic', () => {
    const paths = planCandidatePaths(start, [], slot, 5)
    expect(paths.length).toBeLessThanOrEqual(3)
    expect(paths.map((p) => p.label)).toEqual(['A', 'B', 'C'].slice(0, paths.length))
    const signatures = new Set(paths.map((p) => JSON.stringify(p.steps)))
    expect(signatures.size).toBe(paths.length)
    for (let i = 1; i < paths.length; i++) {
      expect(paths[i].cost).toBeGreaterThanOrEqual(paths[i - 1].cost)
    }
    expect(planCandidatePaths(start, [], slot, 5)).toEqual(paths)
  })

  it('returns no path when the slot itself is blocked', () => {
    const box: ObstacleBox = {
      id: 'crate',
      x: slot.x,
      y: slot.y,
      yaw: 0,
      length: 1.2,
      width: 1.2,
      moving: false,
    }
    expect(planCandidatePaths(start, [box], slot)).toEqual([])
  })

  it('returns no path when already parked', () => {
    expect(planCandidatePaths({ x: slot.x, y: slot.y, yaw: 90 }, [], slot)).toEqual([])
  })
})
