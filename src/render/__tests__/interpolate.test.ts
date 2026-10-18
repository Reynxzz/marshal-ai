import { describe, expect, it } from 'vitest'
import type { CarSnapshot } from '#/sim/vehicle'
import { interpolatePose } from '../interpolate'

const car: CarSnapshot = {
  vin: 'VIN-TEST-001',
  x: 0,
  y: 0,
  yaw: 0,
  speedKmh: 0,
  state: 'moving',
  colour: '#2f6fed',
  lamps: { headlights: true, brake_lights: false },
}

describe('interpolatePose', () => {
  it('interpolates position linearly at the midpoint', () => {
    const pose = interpolatePose({ ...car, x: 10, y: 20 }, { ...car, x: 30, y: 60 }, 0.5)
    expect(pose.x).toBe(20)
    expect(pose.y).toBe(40)
  })

  it('takes the shortest arc across +/- PI', () => {
    const prev = { ...car, yaw: Math.PI - 0.1 }
    const next = { ...car, yaw: -Math.PI + 0.1 }
    const pose = interpolatePose(prev, next, 0.5)
    const wrapped = Math.atan2(Math.sin(pose.yaw), Math.cos(pose.yaw))

    expect(Math.abs(wrapped)).toBeCloseTo(Math.PI, 5)
  })
})
