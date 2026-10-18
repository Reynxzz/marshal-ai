import { describe, expect, it } from 'vitest'
import { ThreeJsEngine } from '../engine'
import { carTarget, LIDAR, lidarScan, propTarget, rayBox } from '../sensors'

describe('lidar', () => {
  it('measures the distance to a box along a ray', () => {
    const box = carTarget('A', 10, 0, 0)
    // The car is 4.9 m long: its rear face is 2.45 m before its centre.
    expect(rayBox(0, 0, 1, 0, box)).toBeCloseTo(7.55, 5)
    expect(rayBox(0, 0, -1, 0, box)).toBeNull()
    expect(rayBox(0, 0, 0, 1, box)).toBeNull()
  })

  it('draws ground rings where the down-looking channels meet the floor', () => {
    const scan = lidarScan({ x: 0, y: 0, yaw: 0 }, [], { beams: 4 })
    const ring = LIDAR.mount_m / Math.tan((15 * Math.PI) / 180)
    const first = scan.hits.filter((hit) => hit.kind === 'ground')[0]
    expect(Math.hypot(first.x, first.y)).toBeCloseTo(ring, 5)
    expect(scan.nearest).toBeNull()
  })

  it('returns off a worker ahead before the floor behind it', () => {
    const worker = propTarget('W', 'worker', 6, 0, 0, { length: 0.6, width: 0.6, height: 1.7 })
    const scan = lidarScan({ x: 0, y: 0, yaw: 0 }, [worker], { beams: 8 })
    expect(scan.nearest?.kind).toBe('worker')
    expect(scan.nearest?.x).toBeCloseTo(5.7, 5)
  })
})

describe('engine telemetry', () => {
  it('reports speed, gear, route and the lidar of a driving car', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('V1', { at: 'line_end', colour: [0, 0, 1] })
    engine.spawnProp('box-1', 'box', 26, 5.25, 0)
    engine.send({
      op: 'mission.go',
      mission_id: 'M1',
      vin: 'V1',
      waypoints: ['inspection_gate'],
      max_speed_kmh: 10,
    })
    for (let i = 0; i < 20; i++) engine.tick(0.25)
    const t = engine.telemetryOf('V1')
    expect(t?.gear).toBe('D')
    expect(t?.speed_kmh).toBeGreaterThan(1)
    expect(t?.odometer_m).toBeGreaterThan(1)
    expect(t?.route?.next).toBe('inspection_gate')
    expect(t?.obstacle?.kind).toBe('box')
    const scan = engine.scan('V1')
    expect(scan.nearest?.kind).toBe('box')
    expect(engine.telemetryOf('nope')).toBeNull()
  })
})
