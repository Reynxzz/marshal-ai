import { describe, expect, it } from 'vitest'
import {
  CAR_LENGTH_M,
  CAR_WHEELBASE_M,
  CAR_WIDTH_M,
  type CarFault,
  createCarMesh,
  effectiveLamps,
  setCarFaults,
  setCarLamps,
  updateCarMesh,
} from '../actors/car'

describe('engine car', () => {
  it('matches the TEAM_PLAN dimensions', () => {
    expect(CAR_LENGTH_M).toBe(4.9)
    expect(CAR_WIDTH_M).toBe(1.9)
    expect(CAR_WHEELBASE_M).toBe(2.85)
  })

  it('turns a faulty lamp off while the others stay on', () => {
    const state = {
      headlights: true,
      brake_lights: true,
      reverse: false,
      indicator_left: true,
      indicator_right: false,
    }
    const effective = effectiveLamps(state, new Set<CarFault>(['headlights']))
    expect(effective).toMatchObject({
      headlights: false,
      brake_lights: true,
      reverse: false,
      indicator_left: true,
      indicator_right: false,
    })
  })

  it('applies the pose and drives the emissive lamp materials', () => {
    const car = createCarMesh('#4f6b8f')
    expect(car.children.length).toBeGreaterThan(8)
    updateCarMesh(car, { x: 10, y: -20, yaw: 90 })
    expect(car.position.x).toBe(10)
    expect(car.position.y).toBe(0)
    expect(car.position.z).toBe(-20)
    expect(car.rotation.y).toBeCloseTo(-Math.PI / 2)

    const lamps = car.userData.parts.lamps
    setCarLamps(car, {
      headlights: true,
      brake_lights: false,
      reverse: false,
      indicator_left: false,
      indicator_right: false,
    })
    expect(lamps.headlights[0].emissiveIntensity).toBeGreaterThan(1)

    setCarFaults(car, new Set<CarFault>(['headlights']))
    expect(lamps.headlights[0].emissiveIntensity).toBeLessThan(0.1)

    setCarFaults(car, new Set<CarFault>())
    expect(lamps.headlights[0].emissiveIntensity).toBeGreaterThan(1)
  })
})
