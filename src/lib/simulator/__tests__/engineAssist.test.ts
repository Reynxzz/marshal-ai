import { describe, expect, it } from 'vitest'
import { POINTS } from '#/lib/marshal/points'
import type { AssistRequestEvent, MissionDoneEvent } from '#/lib/marshal/types'
import { ThreeJsEngine } from '../engine'

function runUntil(engine: ThreeJsEngine, done: () => boolean, maxSeconds = 600): boolean {
  for (let elapsed = 0; elapsed < maxSeconds; elapsed += 0.5) {
    if (done()) return true
    engine.tick(0.5)
  }
  return done()
}

function armAssist(engine: ThreeJsEngine) {
  const requests: AssistRequestEvent[] = []
  engine.on('assist.request', (event) => requests.push(event))
  let done = false
  engine.on('mission.done', (event: MissionDoneEvent) => {
    if (event.mission_id === 'M1') done = true
  })
  engine.spawn('VIN-1', { at: 'line_end', colour: [0, 0, 1] })
  engine.spawnProp('box-1', 'box', 20, POINTS.line_end.y, 0)
  engine.send({ op: 'mission.go', mission_id: 'M1', vin: 'VIN-1', waypoints: ['inspection_gate'] })
  return { requests, finished: () => done }
}

describe('obstacle scan and assist flow (M6)', () => {
  it('stops for a box on the lane instead of driving through it (M6.2)', () => {
    const engine = new ThreeJsEngine()
    const { finished } = armAssist(engine)
    for (let step = 0; step < 120; step++) engine.tick(0.5)
    const pose = engine.getPositionXY('VIN-1')
    expect(pose.x).toBeGreaterThan(14)
    expect(pose.x).toBeLessThan(19)
    expect(finished()).toBe(false)
  })

  it('raises an assist case with the clip after 5 s blocked (M6.3)', () => {
    const engine = new ThreeJsEngine(undefined, {
      assistFrames: () => ['f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8'],
    })
    const { requests } = armAssist(engine)
    for (let step = 0; step < 120; step++) engine.tick(0.5)
    expect(requests).toHaveLength(1)
    expect(requests[0].case_id).toBe('AC-VIN-1-1')
    expect(requests[0].reason).toContain('blocking')
    expect(requests[0].options).toEqual(['wait_for_removal', 'pass_left_3kmh', 'reroute_lane_c'])
    expect(requests[0].clip_frames).toHaveLength(8)
    expect(engine.getAssistMedia('AC-VIN-1-1')).toHaveLength(8)
  })

  it('waits for removal, records the wait and resumes (M6.4)', () => {
    const engine = new ThreeJsEngine()
    const { requests, finished } = armAssist(engine)
    expect(runUntil(engine, () => requests.length > 0)).toBe(true)
    const caseId = requests[0].case_id
    engine.send({
      op: 'assist.choose',
      case_id: caseId,
      vin: 'VIN-1',
      option: 'wait_for_removal',
      chosen_by: 'supervisor',
    })
    const held = engine.getPositionXY('VIN-1').x
    for (let step = 0; step < 20; step++) engine.tick(0.5)
    expect(engine.getPositionXY('VIN-1').x).toBeCloseTo(held, 5)
    engine.removeProp('box-1')
    expect(runUntil(engine, finished)).toBe(true)
    expect(engine.assistOutcomes[caseId]).toMatchObject({ option: 'wait_for_removal' })
    expect(
      Number((engine.assistOutcomes[caseId] as { waited_s?: number }).waited_s),
    ).toBeGreaterThan(0)
  })

  it('passes the box on the left and records the closest distance (M6.4)', () => {
    const engine = new ThreeJsEngine()
    const { requests, finished } = armAssist(engine)
    expect(runUntil(engine, () => requests.length > 0)).toBe(true)
    const caseId = requests[0].case_id
    engine.send({
      op: 'assist.choose',
      case_id: caseId,
      vin: 'VIN-1',
      option: 'pass_left_3kmh',
      chosen_by: 'supervisor',
    })
    let maxOffset = 0
    for (let step = 0; step < 400 && !finished(); step++) {
      engine.tick(0.5)
      maxOffset = Math.max(maxOffset, Math.abs(engine.getPositionXY('VIN-1').y - POINTS.line_end.y))
    }
    expect(finished()).toBe(true)
    expect(maxOffset).toBeGreaterThan(1.5)
    const outcome = engine.assistOutcomes[caseId] as { closest_m?: number; contact?: boolean }
    expect(outcome.contact).toBe(false)
    expect(Number(outcome.closest_m)).toBeGreaterThan(1)
  })

  it('reroutes through Lane C on request (M6.4)', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-1', { at: 'line_end', colour: [0, 0, 1] })
    let done = false
    engine.on('mission.done', (event: MissionDoneEvent) => {
      if (event.mission_id === 'M1') done = true
    })
    engine.send({ op: 'mission.go', mission_id: 'M1', vin: 'VIN-1', waypoints: ['yard.entry'] })
    expect(runUntil(engine, () => engine.getPositionXY('VIN-1').x > 40, 120)).toBe(true)
    engine.send({
      op: 'assist.choose',
      case_id: 'AC-VIN-1-1',
      vin: 'VIN-1',
      option: 'reroute_lane_c',
      chosen_by: 'gemini',
    })
    let minLaneC = Number.POSITIVE_INFINITY
    for (let step = 0; step < 1200 && !done; step++) {
      engine.tick(0.5)
      const pose = engine.getPositionXY('VIN-1')
      minLaneC = Math.min(minLaneC, Math.hypot(pose.x - POINTS.lane_c.x, pose.y - POINTS.lane_c.y))
    }
    expect(done).toBe(true)
    expect(minLaneC).toBeLessThan(20)
    const pose = engine.getPositionXY('VIN-1')
    expect(
      Math.hypot(pose.x - POINTS['yard.entry'].x, pose.y - POINTS['yard.entry'].y),
    ).toBeLessThanOrEqual(3.5 + 1e-6)
  })

  it('answers truth hints for the offline AI', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-1', { at: 'line_end', colour: [1, 0, 0] })
    expect(engine.gateHints('VIN-1').front).toBe('colour=red')
    engine.setFault('VIN-1', new Set(['headlights']))
    expect(engine.evidenceHint('VIN-1', 'front')).toBe('headlights=off')
    expect(engine.evidenceHint('VIN-1', 'rear')).toBe('brake_lights=on')
    engine.spawnProp('worker-1', 'worker', POINTS.line_end.x + 2, POINTS.line_end.y + 1, 90)
    engine.raiseAssist('VIN-1', 'AC-VIN-1-1', 'box and worker', ['wait_for_removal'])
    expect(engine.assistHint('AC-VIN-1-1')).toBe('worker=1')
  })
})
