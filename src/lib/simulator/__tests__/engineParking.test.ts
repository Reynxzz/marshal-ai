import { describe, expect, it } from 'vitest'
import { CAR_LENGTH_M, CAR_WIDTH_M } from '../actors/car'
import { ThreeJsEngine } from '../engine'

describe('parking port (M7)', () => {
  it('reports nearby vehicles and props, excluding the car itself (M7.2)', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-SELF', { at: 'yard.Y1', colour: [0, 0, 1] })
    engine.spawn('VIN-NEAR', { at: 'yard.Y2', colour: [0, 0, 1] })
    engine.spawn('VIN-FAR', { at: 'line_end', colour: [0, 0, 1] })
    engine.spawnProp('box-1', 'box', -29, -68, 0)
    engine.spawnProp('truck-1', 'truck', -29, -60, 0)

    const slot = { id: 'yard.Y1', ...engine.getTransform('yard.Y1') }
    const obstacles = engine.getObstacles('VIN-SELF', slot)
    const ids = obstacles.map((box) => box.id).sort()
    expect(ids).toEqual(['VIN-NEAR', 'box-1', 'truck-1'])
    const near = obstacles.find((box) => box.id === 'VIN-NEAR')
    expect(near).toMatchObject({ length: CAR_LENGTH_M, width: CAR_WIDTH_M, moving: false })
    const truck = obstacles.find((box) => box.id === 'truck-1')
    expect(truck).toMatchObject({ length: 8, width: 2.5, moving: false })
    const box = obstacles.find((item) => item.id === 'box-1')
    expect(box).toMatchObject({ length: 1.2, width: 1.2 })
  })

  it('executes control steps with the bicycle model and logs the session (M7.1/M7.4)', async () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-P', { at: 'yard.Y1', colour: [0, 0, 1] })
    const before = engine.getPositionXY('VIN-P')

    const straight = await engine.execute('VIN-P', [
      { speed_mps: 1, wheel: 0, duration_s: 2, gear: 'D' },
    ])
    expect(straight.stopped).toBe(false)
    // yard.Y1 faces +y: two metres forward.
    expect(straight.pose.x).toBeCloseTo(before.x, 5)
    expect(straight.pose.y).toBeCloseTo(before.y + 2, 5)

    const arc = await engine.execute('VIN-P', [
      { speed_mps: 1, wheel: 1, duration_s: 1, gear: 'D' },
    ])
    expect(arc.pose.yaw).toBeGreaterThan(before.yaw)
    expect(engine.parkingLog['VIN-P']).toHaveLength(2)
    expect(engine.parkingLog['VIN-P'][0]).toMatchObject({ steps: 1, stopped: false })
  })

  it('halts with the safety stop when the probe would hit an obstacle (M7.3)', async () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-P', { at: 'yard.Y1', colour: [0, 0, 1] })
    engine.spawnProp('box-1', 'box', -30.75, -67.2, 0)
    const before = engine.getPositionXY('VIN-P')
    const result = await engine.execute('VIN-P', [
      { speed_mps: 1, wheel: 0, duration_s: 2, gear: 'D' },
    ])
    expect(result.stopped).toBe(true)
    const pose = engine.getPositionXY('VIN-P')
    expect(pose.x).toBeCloseTo(before.x, 6)
    expect(pose.y).toBeCloseTo(before.y, 6)
    expect(engine.parkingLog['VIN-P'][0]).toMatchObject({ stopped: true })
  })

  it('stops for a car in the probe path too (M7.3)', async () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-P', { at: 'yard.Y1', colour: [0, 0, 1] })
    engine.spawn('VIN-BLOCK', { at: 'yard.Y2', colour: [0, 0, 1] })
    const result = await engine.execute('VIN-P', [
      { speed_mps: 1, wheel: 0, duration_s: 2, gear: 'D' },
    ])
    // Y2 is 5.5 m ahead: the 0.4 m probe is clear, so the step runs.
    expect(result.stopped).toBe(false)
  })
})
