import { describe, expect, it } from 'vitest'
import { isPointName, POINTS, YARD_SLOTS } from '../points'

describe('POINTS', () => {
  it('matches the TEAM_PLAN map table', () => {
    expect(POINTS.line_end).toEqual({ x: 8.0, y: 5.25, yaw: 0 })
    expect(POINTS.buffer_zone_b).toEqual({ x: 18.0, y: 5.25, yaw: 0 })
    expect(POINTS.inspection_gate).toEqual({ x: 32.0, y: 5.25, yaw: 0 })
    expect(POINTS.test_track).toEqual({ x: 62.0, y: 5.25, yaw: 0 })
    expect(POINTS.report_point).toEqual({ x: 100.0, y: 5.25, yaw: 0 })
    expect(POINTS.lane_c).toEqual({ x: 117.25, y: -50.0, yaw: -90 })
    expect(POINTS.finishing).toEqual({ x: 90.0, y: -109.25, yaw: 180 })
    expect(POINTS['rework.electrical']).toEqual({ x: 150.75, y: -40.0, yaw: -90 })
    expect(POINTS['yard.entry']).toEqual({ x: -27.25, y: -80.0, yaw: 90 })
  })

  it('has 8 yard slots from -68.0 to -29.5, 5.5m apart', () => {
    expect(YARD_SLOTS).toHaveLength(8)
    const ys = YARD_SLOTS.map((slot) => POINTS[slot].y)
    expect(ys[0]).toBe(-68.0)
    expect(ys.at(-1)).toBe(-29.5)
    for (let i = 1; i < ys.length; i++) expect(ys[i] - ys[i - 1]).toBeCloseTo(5.5, 10)
    for (const slot of YARD_SLOTS) {
      expect(POINTS[slot].x).toBe(-30.75)
      expect(POINTS[slot].yaw).toBe(90)
    }
  })

  it('recognises named points only', () => {
    expect(isPointName('yard.Y8')).toBe(true)
    expect(isPointName('yard.Y9')).toBe(false)
    expect(isPointName('toString')).toBe(false)
  })
})
