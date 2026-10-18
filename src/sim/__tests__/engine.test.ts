import { describe, expect, it } from 'vitest'
import { FIXED_DT, SimEngine } from '../engine'
import type { SimEvent } from '../events'
import { world } from '../world'

function runUntil(engine: SimEngine, cond: () => boolean, maxTicks = 20000): number {
  let ticks = 0
  while (!cond() && ticks < maxTicks) {
    engine.step(FIXED_DT)
    ticks++
  }
  return ticks
}

describe('SimEngine', () => {
  it('drives one car line_end -> gate -> track -> report -> yard', () => {
    const engine = new SimEngine(world, { seed: 1 })
    const events: SimEvent[] = []
    engine.on((e) => events.push(e))
    engine.spawn('V1')
    engine.send({
      type: 'go',
      vin: 'V1',
      mission: {
        id: 'M1',
        waypoints: ['inspection_gate', 'test_track', 'report_point', 'yard.Y1'],
      },
    })
    runUntil(engine, () => events.some((e) => e.type === 'MissionDone' && e.missionId === 'M1'))
    expect(engine.get('V1')?.location).toBe('yard.Y1')
  })

  it('runs routine R-12 steps in order with telemetry', () => {
    const engine = new SimEngine(world, { seed: 2 })
    const steps: string[] = []
    engine.on((e) => {
      if (e.type === 'TrackStep') steps.push(e.name)
    })
    engine.spawn('V1')
    engine.send({
      type: 'go',
      vin: 'V1',
      mission: { id: 'M1', waypoints: ['test_track'], routine: 'R-12' },
    })
    let done = false
    engine.on((e) => {
      if (e.type === 'RoutineDone') done = true
    })
    runUntil(engine, () => done)
    expect(steps).toEqual([
      'headlights_on',
      'headlights_off',
      'brake_lights',
      'reverse',
      'brake_test',
    ])
  })

  it('holds for 30 simulated seconds then resumes', () => {
    const engine = new SimEngine(world, { seed: 3 })
    engine.spawn('V1')
    engine.send({ type: 'go', vin: 'V1', mission: { id: 'M1', waypoints: ['report_point'] } })
    for (let i = 0; i < 50; i++) engine.step(FIXED_DT)
    engine.send({ type: 'hold', vin: 'V1', durationS: 30 })
    const before = engine.get('V1')?.distance ?? 0
    for (let i = 0; i < 100; i++) engine.step(FIXED_DT)
    expect(engine.get('V1')?.distance).toBe(before)
    for (let i = 0; i < 250; i++) engine.step(FIXED_DT)
    expect(engine.get('V1')?.distance ?? 0).toBeGreaterThan(before)
  })

  it('stops on assist and resumes', () => {
    const engine = new SimEngine(world, { seed: 3 })
    engine.spawn('V1')
    engine.send({ type: 'go', vin: 'V1', mission: { id: 'M1', waypoints: ['report_point'] } })
    for (let i = 0; i < 50; i++) engine.step(FIXED_DT)
    engine.stop('V1', 'assist')
    const before = engine.get('V1')?.distance ?? 0
    for (let i = 0; i < 200; i++) engine.step(FIXED_DT)
    expect(engine.get('V1')?.distance).toBe(before)
    engine.resume('V1')
    for (let i = 0; i < 50; i++) engine.step(FIXED_DT)
    expect(engine.get('V1')?.distance ?? 0).toBeGreaterThan(before)
  })

  it('keeps 6 cars from overlapping in the queue', () => {
    const engine = new SimEngine(world, { seed: 4 })
    for (let i = 0; i < 6; i++) {
      engine.spawn(`V${i}`)
      engine.send({
        type: 'go',
        vin: `V${i}`,
        mission: { id: `M${i}`, waypoints: ['inspection_gate'] },
      })
    }
    for (let tick = 0; tick < 300; tick++) {
      engine.step(FIXED_DT)
      const snap = engine.snapshot()
      for (let a = 0; a < snap.length; a++) {
        for (let b = a + 1; b < snap.length; b++) {
          const d = Math.hypot(snap[a].x - snap[b].x, snap[a].y - snap[b].y)
          expect(d).toBeGreaterThan(1)
        }
      }
    }
  })

  it('is deterministic across runs (200 ticks, identical event log)', () => {
    const build = () => {
      const engine = new SimEngine(world, { seed: 7 })
      const log: SimEvent[] = []
      engine.on((e) => log.push(e))
      for (let i = 0; i < 3; i++) {
        engine.spawn(`V${i}`)
        engine.send({
          type: 'go',
          vin: `V${i}`,
          mission: { id: `M${i}`, waypoints: ['inspection_gate', 'test_track'] },
        })
      }
      for (let tick = 0; tick < 200; tick++) engine.step(FIXED_DT)
      return log
    }
    expect(build()).toEqual(build())
  })
})
