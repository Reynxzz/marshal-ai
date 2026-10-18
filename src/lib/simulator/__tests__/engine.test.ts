import { describe, expect, it } from 'vitest'
import { POINTS } from '#/lib/marshal/points'
import type { MissionDoneEvent } from '#/lib/marshal/types'
import { ThreeJsEngine } from '../engine'

function runUntil(engine: ThreeJsEngine, done: () => boolean, maxSeconds = 600): boolean {
  for (let elapsed = 0; elapsed < maxSeconds; elapsed += 0.5) {
    if (done()) return true
    engine.tick(0.5)
  }
  return done()
}

function drive(
  engine: ThreeJsEngine,
  vin: string,
  missionId: string,
  waypoints: string[],
): boolean {
  let done = false
  engine.on('mission.done', (event: MissionDoneEvent) => {
    if (event.mission_id === missionId) done = true
  })
  engine.send({ op: 'mission.go', mission_id: missionId, vin, waypoints })
  return runUntil(engine, () => done)
}

describe('ThreeJsEngine', () => {
  it('runs the acceptance loop and crosses the gate at ~5 km/h', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-1', { at: 'line_end', colour: [0, 0, 1] })

    expect(drive(engine, 'VIN-1', 'M1', ['buffer_zone_b', 'inspection_gate'])).toBe(true)
    const gate = engine.getPositionXY('VIN-1')
    expect(
      Math.hypot(gate.x - POINTS.inspection_gate.x, gate.y - POINTS.inspection_gate.y),
    ).toBeLessThanOrEqual(3.5 + 1e-6)
    expect(engine.passes['VIN-1']).toBeGreaterThanOrEqual(4)
    expect(engine.passes['VIN-1']).toBeLessThanOrEqual(6.5)

    expect(drive(engine, 'VIN-1', 'M2', ['test_track'])).toBe(true)
    expect(drive(engine, 'VIN-1', 'M3', ['report_point'])).toBe(true)
    const report = engine.getPositionXY('VIN-1')
    expect(
      Math.hypot(report.x - POINTS.report_point.x, report.y - POINTS.report_point.y),
    ).toBeLessThanOrEqual(3.5 + 1e-6)
  })

  it('cruises at the mission speed', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-1', { at: 'line_end', colour: [0, 0, 1] })
    let top = 0
    engine.on('status', (event) => {
      if (event.vin === 'VIN-1') top = Math.max(top, event.speed_kmh)
    })
    expect(drive(engine, 'VIN-1', 'M1', ['test_track'])).toBe(true)
    expect(top).toBeGreaterThan(9)
    expect(top).toBeLessThanOrEqual(10.5)
  })

  it('emits status about once per second', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-1', { at: 'line_end', colour: [0, 0, 1] })
    let count = 0
    engine.on('status', () => {
      count++
    })
    engine.send({
      op: 'mission.go',
      mission_id: 'M-status',
      vin: 'VIN-1',
      waypoints: ['report_point'],
    })
    for (let step = 0; step < 100; step++) engine.tick(0.1)
    expect(count).toBeGreaterThanOrEqual(9)
    expect(count).toBeLessThanOrEqual(11)
  })

  it('squares up onto the pull-over pose with zero error (M4.2)', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-P', { at: 'line_end', colour: [1, 0, 0] })
    expect(drive(engine, 'VIN-P', 'M1', ['rework.electrical'])).toBe(true)
    const pose = engine.getPositionXY('VIN-P')
    const target = POINTS['rework.electrical']
    expect(pose.x).toBeCloseTo(target.x, 6)
    expect(pose.y).toBeCloseTo(target.y, 6)
    expect(pose.yaw).toBeCloseTo(target.yaw, 6)
  })

  it('holds a car until the hold expires', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-1', { at: 'line_end', colour: [0, 0, 1] })
    engine.send({
      op: 'mission.go',
      mission_id: 'M-hold',
      vin: 'VIN-1',
      waypoints: ['buffer_zone_b'],
    })
    engine.send({ op: 'mission.hold', vin: 'VIN-1', duration_s: 5, reason: 'test hold' })
    for (let step = 0; step < 10; step++) engine.tick(0.5)
    expect(engine.getPositionXY('VIN-1').x).toBeCloseTo(POINTS.line_end.x, 5)
    expect(
      runUntil(engine, () => engine.getPositionXY('VIN-1').x > POINTS.buffer_zone_b.x - 3.5, 60),
    ).toBe(true)
  })

  it('is deterministic for the same commands and steps', () => {
    const first = new ThreeJsEngine()
    const second = new ThreeJsEngine()
    for (const engine of [first, second]) {
      engine.spawn('VIN-1', { at: 'line_end', colour: [0, 0, 1] })
      engine.send({
        op: 'mission.go',
        mission_id: 'M-det',
        vin: 'VIN-1',
        waypoints: ['buffer_zone_b', 'inspection_gate'],
      })
    }
    for (let step = 0; step < 200; step++) {
      first.tick(0.1)
      second.tick(0.1)
    }
    expect(first.getPositionXY('VIN-1')).toEqual(second.getPositionXY('VIN-1'))
    expect(first.time).toBe(second.time)
  })
})

describe('multi-car queue and yard moves (M4)', () => {
  it('waits 6 m behind a held car and resumes with it (M4.1)', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-A', { at: 'line_end', colour: [0, 0, 1] })
    engine.spawn('VIN-B', { at: 'line_end', colour: [0, 0, 1], behind_m: 9 })
    for (const vin of ['VIN-A', 'VIN-B']) {
      engine.send({
        op: 'mission.go',
        mission_id: `M-${vin}`,
        vin,
        waypoints: ['buffer_zone_b', 'inspection_gate'],
      })
    }
    engine.send({ op: 'mission.hold', vin: 'VIN-A', duration_s: 8, reason: 'test hold' })

    let minGap = Number.POSITIVE_INFINITY
    for (let step = 0; step < 70; step++) {
      engine.tick(0.1)
      minGap = Math.min(minGap, engine.getPositionXY('VIN-A').x - engine.getPositionXY('VIN-B').x)
    }
    expect(minGap).toBeGreaterThanOrEqual(6 - 1e-9)
    const gap = engine.getPositionXY('VIN-A').x - engine.getPositionXY('VIN-B').x
    expect(gap).toBeGreaterThanOrEqual(6)
    expect(gap).toBeLessThanOrEqual(10)

    const startX = engine.getPositionXY('VIN-B').x
    expect(runUntil(engine, () => engine.getPositionXY('VIN-B').x > startX + 1, 60)).toBe(true)
  })

  it('ignores cars parked on the yard row when moving along it (M4.1)', () => {
    // shortcut: static parked cars are not lane obstacles yet, so the move passes them.
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-PARKED', { at: 'yard.Y5', colour: [0, 0, 0] })
    engine.spawn('VIN-Q', { at: 'yard.Y3', colour: [0, 0, 1] })
    expect(drive(engine, 'VIN-Q', 'M1', ['yard.Y7'])).toBe(true)
    const pose = engine.getPositionXY('VIN-Q')
    expect(pose.x).toBeCloseTo(POINTS['yard.Y7'].x, 5)
    expect(pose.y).toBeCloseTo(POINTS['yard.Y7'].y, 5)
  })

  it('pulls out 5 m into the lane when leaving a slot (M4.3)', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-S', { at: 'yard.Y2', colour: [0, 0, 1] })
    engine.send({ op: 'mission.go', mission_id: 'M1', vin: 'VIN-S', waypoints: ['yard.entry'] })
    const pose = engine.getPositionXY('VIN-S')
    expect(pose.x).toBeCloseTo(POINTS['yard.Y2'].x, 6)
    expect(pose.y).toBeCloseTo(POINTS['yard.Y2'].y + 5, 6)
  })

  it('drives slot-to-slot forward when the target is ahead (M4.4)', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-F', { at: 'yard.Y3', colour: [0, 0, 1] })
    expect(drive(engine, 'VIN-F', 'F1', ['yard.Y6'])).toBe(true)
    const pose = engine.getPositionXY('VIN-F')
    expect(pose.x).toBeCloseTo(POINTS['yard.Y6'].x, 5)
    expect(pose.y).toBeCloseTo(POINTS['yard.Y6'].y, 5)
  })

  it('reverses straight to a slot behind without turning (M4.4)', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-R', { at: 'yard.Y6', colour: [0, 0, 1] })
    let done = false
    engine.on('mission.done', (event: MissionDoneEvent) => {
      if (event.mission_id === 'R1') done = true
    })
    engine.send({ op: 'mission.go', mission_id: 'R1', vin: 'VIN-R', waypoints: ['yard.Y3'] })
    let maxDrift = 0
    for (let step = 0; step < 400 && !done; step++) {
      engine.tick(0.5)
      maxDrift = Math.max(maxDrift, Math.abs(engine.getPositionXY('VIN-R').yaw - 90))
    }
    expect(done).toBe(true)
    expect(maxDrift).toBeLessThan(1e-6)
    const pose = engine.getPositionXY('VIN-R')
    expect(pose.x).toBeCloseTo(POINTS['yard.Y3'].x, 5)
    expect(pose.y).toBeCloseTo(POINTS['yard.Y3'].y, 5)
  })
})

describe('R-12 routine and faults (M5)', () => {
  function runRoutine(steps?: number[]) {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-R', { at: 'line_end', colour: [0, 0, 1] })
    const events: Array<{ step: number; name: string; telemetry: Record<string, unknown> }> = []
    engine.on('track.step', (event) => events.push(event))
    let done = false
    engine.on('mission.done', (event: MissionDoneEvent) => {
      if (event.mission_id === 'M-routine') done = true
    })
    engine.send({
      op: 'mission.go',
      mission_id: 'M-routine',
      vin: 'VIN-R',
      waypoints: ['test_track'],
      routine: 'R-12',
      ...(steps ? { routine_steps: steps } : {}),
    })
    const finished = runUntil(engine, () => done)
    return { engine, events, finished }
  }

  it('runs the six steps at the test track and emits every track.step', () => {
    const { engine, events, finished } = runRoutine()
    expect(finished).toBe(true)
    expect(events.map((event) => event.step)).toEqual([1, 2, 3, 4, 5, 6])
    expect(events.map((event) => event.name)).toEqual([
      'headlights',
      'indicators',
      'brake_lights',
      'reverse',
      'brake_test',
      'full_lock',
    ])
    expect(events[0].telemetry).toEqual({ headlights: 'ON' })
    expect(events[3].telemetry.gear).toBe('R')
    expect(Number(events[3].telemetry.distance_m)).toBeCloseTo(3.02, 1)
    expect(Number(events[4].telemetry.start_speed_kmh)).toBeGreaterThanOrEqual(19)
    expect(Number(events[4].telemetry.stopping_distance_m)).toBeGreaterThan(3)
    expect(engine.lampsOf('VIN-R')).toEqual({
      headlights: false,
      brake_lights: false,
      reverse: false,
      indicator_left: false,
      indicator_right: false,
    })
  })

  it('runs only the requested re-check steps (M5.6)', () => {
    const { events, finished } = runRoutine([1])
    expect(finished).toBe(true)
    expect(events.map((event) => event.step)).toEqual([1])
  })

  it('keeps step telemetry nominal while a lamp is faulty (M5.2/M5.3)', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-D', { at: 'line_end', colour: [0, 0, 1] })
    engine.setFault('VIN-D', new Set(['headlights']))
    let first: Record<string, unknown> | null = null
    engine.on('track.step', (event) => {
      if (event.step === 1) first = event.telemetry
    })
    let done = false
    engine.on('mission.done', (event: MissionDoneEvent) => {
      if (event.mission_id === 'M-dark') done = true
    })
    engine.send({
      op: 'mission.go',
      mission_id: 'M-dark',
      vin: 'VIN-D',
      waypoints: ['test_track'],
      routine: 'R-12',
      routine_steps: [1],
    })
    expect(runUntil(engine, () => done)).toBe(true)
    expect(first).toEqual({ headlights: 'ON' })
    expect(engine.faultsOf('VIN-D')).toEqual(['headlights'])
  })

  it('clears faults on repair (M5.4)', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-F', { at: 'line_end', colour: [0, 0, 1] })
    engine.setFault('VIN-F', new Set(['headlights']))
    expect(engine.faultsOf('VIN-F')).toEqual(['headlights'])
    engine.repair('VIN-F', ['headlights'])
    expect(engine.faultsOf('VIN-F')).toEqual([])
  })
})
