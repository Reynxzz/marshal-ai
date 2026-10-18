// Regression: cars in the scripted engine queue instead of overlapping. Every scenario samples
// every live car's pose every 0.25 sim s and fails on any intersecting footprints (or two cars
// under 5 m apart in the same lane).

import { afterEach, describe, expect, it } from 'vitest'
import {
  CREW_CLEAR_S,
  DEFAULT_SCENARIO,
  getShiftBackend,
  type ShiftScenario,
  useShiftStore,
} from '#/stores/shift'
import type { CarSpec } from '../orchestrator'
import { POINTS } from '../points'
import type { MarshalState, Truck } from '../state'
import { createOfflineMarshalAi } from '../testing/offlineAi'
import { CAR_LENGTH_M, carRect, rectsOverlap } from '../testing/overlap'
import { IDLE_QUEUE_S, ScriptedDrivingSystem, STANDING_GAP_M } from '../testing/scriptedSystem'
import { flush, OverlapMonitor, setup, simLatency } from './harness'

const SAMPLE_S = 0.25

afterEach(() => {
  useShiftStore.getState().configure({ ai: null, scenario: DEFAULT_SCENARIO })
})

async function runStoreShift(scenario: ShiftScenario, latency_s: number, maxSeconds = 2400) {
  const engine = () => getShiftBackend() as ScriptedDrivingSystem
  const latency = simLatency(() => engine().time)
  const offline = createOfflineMarshalAi()
  const store = () => useShiftStore.getState()
  store().configure({ ai: latency_s > 0 ? latency.wrap(offline, latency_s) : offline, scenario })
  store().setPauseWhenNeeded(false)
  const monitor = new OverlapMonitor()
  for (let t = 0; t < maxSeconds && store().shift.progress < 1; t += SAMPLE_S) {
    store().advance(SAMPLE_S)
    latency.pump()
    await flush()
    monitor.sample(engine(), store().shift, engine().time)
  }
  return { monitor, shift: store().shift, ds: engine() }
}

async function runOrchestrator(
  cars: CarSpec[],
  opts: {
    trucks?: Truck[]
    reservedSlots?: string[]
    latency_s?: number
    /** Runs before every tick (scripted events). */
    script?: (ds: ScriptedDrivingSystem, state: MarshalState, time: number) => void
    done?: (state: MarshalState) => boolean
    maxSeconds?: number
  } = {},
) {
  let clock = () => 0
  const latency = simLatency(() => clock())
  const offline = createOfflineMarshalAi()
  const ai = opts.latency_s ? latency.wrap(offline, opts.latency_s) : undefined
  const { ds, orch, feedText } = setup({ pauseWhenNeeded: false, ...(ai ? { ai } : {}) })
  clock = () => ds.time
  orch.start(cars, { trucks: opts.trucks, reservedSlots: opts.reservedSlots })
  const monitor = new OverlapMonitor()
  const done = opts.done ?? ((state: MarshalState) => state.progress === 1)
  const maxSeconds = opts.maxSeconds ?? 1200
  for (let t = 0; t < maxSeconds; t += SAMPLE_S) {
    await flush()
    if (done(orch.getState())) break
    opts.script?.(ds, orch.getState(), ds.time)
    ds.tick(SAMPLE_S)
    latency.pump()
    await flush()
    monitor.sample(ds, orch.getState(), ds.time)
  }
  return { monitor, orch, ds, feedText }
}

function expectNoOverlap(monitor: OverlapMonitor) {
  expect(monitor.report()).toEqual([])
}

describe('assist options change what the car does', () => {
  const blue: [number, number, number] = [0, 0, 1]

  function stopAtBox(ds: ScriptedDrivingSystem, waypoints: string[]) {
    ds.spawn('CAR', { at: 'report_point', colour: blue })
    ds.send({ op: 'mission.go', mission_id: 'M1', vin: 'CAR', waypoints })
    ds.tick(4)
    const at = ds.getPositionXY('CAR')
    const yaw = (at.yaw * Math.PI) / 180
    const box = { x: at.x + 6 * Math.cos(yaw), y: at.y + 6 * Math.sin(yaw) }
    ds.spawnProp('box', 'box', box.x, box.y, at.yaw)
    ds.raiseAssist('CAR', 'AC-1', 'Box in the lane', [
      'wait_for_removal',
      'pass_left_3kmh',
      'reroute_lane_c',
    ])
    return { start: at, box }
  }

  function drive(ds: ScriptedDrivingSystem, seconds: number) {
    const poses: { x: number; y: number; yaw: number; speed: number }[] = []
    ds.on('status', (event) => {
      if (event.vin === 'CAR') poses.push({ ...ds.getPositionXY('CAR'), speed: event.speed_kmh })
    })
    for (let t = 0; t < seconds; t += SAMPLE_S) ds.tick(SAMPLE_S)
    return poses
  }

  function clipsBox(pose: { x: number; y: number; yaw: number }, box: { x: number; y: number }) {
    const rect = { x: box.x, y: box.y, yaw: 0, length: 1.2, width: 1.2 }
    return rectsOverlap(carRect(pose), rect)
  }

  it('wait_for_removal: the car stays put until the box is removed', () => {
    const ds = new ScriptedDrivingSystem()
    const { start } = stopAtBox(ds, ['finishing', 'yard.entry'])
    ds.send({
      op: 'assist.choose',
      case_id: 'AC-1',
      vin: 'CAR',
      option: 'wait_for_removal',
      chosen_by: 'supervisor',
    })
    drive(ds, 10)
    expect(ds.getPositionXY('CAR')).toMatchObject({ x: start.x, y: start.y })
    ds.removeProp('box')
    drive(ds, 5)
    expect(ds.getPositionXY('CAR').y).toBeLessThan(start.y - 5)
  })

  it('pass_left_3kmh: the car creeps past on the left at 3 km/h with the box still there', () => {
    const ds = new ScriptedDrivingSystem()
    const { start, box } = stopAtBox(ds, ['finishing', 'yard.entry'])
    const done: string[] = []
    ds.on('mission.done', (event) => done.push(event.location))
    ds.send({
      op: 'assist.choose',
      case_id: 'AC-1',
      vin: 'CAR',
      option: 'pass_left_3kmh',
      chosen_by: 'supervisor',
    })
    const poses = drive(ds, 160)
    expect(done).toEqual(['yard.entry'])
    expect(ds.listProps().map((prop) => prop.id)).toEqual(['box'])
    expect(poses.some((pose) => clipsBox(pose, box))).toBe(false)
    // Beside the box: 3 m to the left of the original line, at walking pace.
    const fx = (box.x - start.x) / 6
    const fy = (box.y - start.y) / 6
    const ahead = (pose: { x: number; y: number }) => (pose.x - box.x) * fx + (pose.y - box.y) * fy
    const left = (pose: { x: number; y: number }) => fx * (pose.y - box.y) - fy * (pose.x - box.x)
    const beside = poses.filter((pose) => Math.abs(ahead(pose)) < 1)
    expect(beside.length).toBeGreaterThan(0)
    for (const pose of beside) {
      expect(left(pose)).toBeCloseTo(3, 1)
      expect(pose.speed).toBeLessThanOrEqual(3.01)
    }
  })

  it('reroute_lane_c: a car heading for the finishing corner goes via lane C', () => {
    const ds = new ScriptedDrivingSystem()
    const { box } = stopAtBox(ds, ['finishing', 'yard.entry'])
    const locations = new Set<string>()
    ds.on('status', (event) => locations.add(event.location))
    ds.send({
      op: 'assist.choose',
      case_id: 'AC-1',
      vin: 'CAR',
      option: 'reroute_lane_c',
      chosen_by: 'supervisor',
    })
    expect(ds.missionOf('CAR')?.waypoints).toEqual(['lane_c', 'yard.entry'])
    const poses = drive(ds, 200)
    expect(locations.has('lane_c')).toBe(true)
    expect(locations.has('finishing')).toBe(false)
    expect(ds.getPositionXY('CAR')).toEqual({ ...POINTS['yard.entry'] })
    expect(poses.some((pose) => clipsBox(pose, box))).toBe(false)
  })

  it('reroute_lane_c elsewhere: the car takes the lane on the right around the box', () => {
    const ds = new ScriptedDrivingSystem()
    const { start, box } = stopAtBox(ds, ['lane_c', 'yard.entry'])
    ds.send({
      op: 'assist.choose',
      case_id: 'AC-1',
      vin: 'CAR',
      option: 'reroute_lane_c',
      chosen_by: 'supervisor',
    })
    const poses = drive(ds, 200)
    expect(ds.getPositionXY('CAR')).toEqual({ ...POINTS['yard.entry'] })
    expect(poses.some((pose) => clipsBox(pose, box))).toBe(false)
    // Beside the box: 4 m to the right of the original line, at mission speed.
    const fx = (box.x - start.x) / 6
    const fy = (box.y - start.y) / 6
    const ahead = (pose: { x: number; y: number }) => (pose.x - box.x) * fx + (pose.y - box.y) * fy
    const left = (pose: { x: number; y: number }) => fx * (pose.y - box.y) - fy * (pose.x - box.x)
    const beside = poses.filter((pose) => Math.abs(ahead(pose)) < 1)
    expect(beside.length).toBeGreaterThan(0)
    for (const pose of beside) {
      expect(left(pose)).toBeCloseTo(-4, 1)
      expect(pose.speed).toBeGreaterThan(9)
    }
  })

  for (const option of ['wait_for_removal', 'pass_left_3kmh', 'reroute_lane_c'] as const) {
    it(`plays the default shift with the supervisor choosing ${option} for P002`, async () => {
      const engine = () => getShiftBackend() as ScriptedDrivingSystem
      const store = () => useShiftStore.getState()
      store().configure({ ai: createOfflineMarshalAi(), scenario: DEFAULT_SCENARIO })
      const monitor = new OverlapMonitor()
      let decidedAt: number | null = null
      let stoppedAt: { x: number; y: number } | null = null
      let moved: number | null = null
      for (let t = 0; t < 2400 && store().shift.progress < 1; t += SAMPLE_S) {
        if (store().shift.cases['AC-P002-1']?.status === 'open' && store().shift.paused) {
          stoppedAt = engine().getPositionXY('P002')
          expect(store().choose('AC-P002-1', option)).toBe(true)
          decidedAt = engine().time
        }
        store().advance(SAMPLE_S)
        await flush()
        monitor.sample(engine(), store().shift, engine().time)
        const pose = engine().getPositionXY('P002')
        if (
          moved === null &&
          stoppedAt &&
          Math.hypot(pose.x - stoppedAt.x, pose.y - stoppedAt.y) > 0.1
        )
          moved = engine().time
      }
      expect(store().shift.progress).toBe(1)
      expectNoOverlap(monitor)
      expect(decidedAt).not.toBeNull()
      expect(moved).not.toBeNull()
      const delay = (moved ?? 0) - (decidedAt ?? 0)
      // Waiting means waiting for the crew; passing / rerouting moves off at once.
      if (option === 'wait_for_removal') expect(delay).toBeGreaterThanOrEqual(CREW_CLEAR_S - 0.5)
      else expect(delay).toBeLessThan(1)
      expect(engine().assistOutcomes['AC-P002-1']).toMatchObject({ option })
    }, 60_000)
  }
})

describe('scripted engine queuing rules', () => {
  const blue: [number, number, number] = [0, 0, 1]

  function tickWatching(ds: ScriptedDrivingSystem, vins: string[], seconds: number) {
    const monitor = new OverlapMonitor()
    const order = [...vins]
    const state = {
      order,
      cars: Object.fromEntries(vins.map((vin) => [vin, { stage: 'to_gate', location: '' }])),
    } as unknown as MarshalState
    for (let t = 0; t < seconds; t += SAMPLE_S) {
      ds.tick(SAMPLE_S)
      monitor.sample(ds, state, ds.time)
    }
    return monitor
  }

  it('stops a follower a standing gap behind a held car, without an assist request', () => {
    const ds = new ScriptedDrivingSystem()
    const assists: unknown[] = []
    ds.on('assist.request', (event) => assists.push(event))
    ds.spawn('A', { at: 'line_end', colour: blue })
    ds.spawn('B', { at: 'line_end', colour: blue, behind_m: 9 })
    ds.send({ op: 'mission.go', mission_id: 'MA', vin: 'A', waypoints: ['inspection_gate'] })
    ds.send({ op: 'mission.go', mission_id: 'MB', vin: 'B', waypoints: ['inspection_gate'] })
    ds.tick(2)
    ds.send({ op: 'mission.hold', vin: 'A', reason: 'test' })
    const monitor = tickWatching(ds, ['A', 'B'], 20)
    const a = ds.getPositionXY('A')
    const b = ds.getPositionXY('B')
    const bumperGap = a.x - b.x - CAR_LENGTH_M
    expect(bumperGap).toBeGreaterThanOrEqual(STANDING_GAP_M - 0.01)
    expect(bumperGap).toBeLessThan(STANDING_GAP_M + 0.5)
    expect(monitor.report()).toEqual([])
    expect(assists).toEqual([])
  })

  it('keeps one car on the test-track pad; the next queues and then drives in', () => {
    const ds = new ScriptedDrivingSystem({ stepSeconds: 2 })
    const done: string[] = []
    ds.on('mission.done', (event) => done.push(`${event.vin}@${event.location}`))
    ds.spawn('A', { at: 'inspection_gate', colour: blue })
    ds.spawn('B', { at: 'inspection_gate', colour: blue, behind_m: 12 })
    const r12 = { routine: 'R-12' as const }
    ds.send({ op: 'mission.go', mission_id: 'MA', vin: 'A', waypoints: ['test_track'], ...r12 })
    ds.send({ op: 'mission.go', mission_id: 'MB', vin: 'B', waypoints: ['test_track'], ...r12 })
    const monitor = tickWatching(ds, ['A', 'B'], 18)
    // A runs R-12 on the pad; B waits behind it.
    expect(ds.getPositionXY('A').x).toBeCloseTo(POINTS.test_track.x)
    expect(ds.getPositionXY('B').x).toBeLessThan(POINTS.test_track.x - CAR_LENGTH_M - 5.9)
    monitor.episodes.push(...tickWatching(ds, ['A', 'B'], 10).episodes)
    ds.send({ op: 'mission.go', mission_id: 'MA2', vin: 'A', waypoints: ['report_point'] })
    monitor.episodes.push(...tickWatching(ds, ['A', 'B'], 40).episodes)
    expect(ds.getPositionXY('B').x).toBeCloseTo(POINTS.test_track.x)
    expect(done).toEqual(['A@test_track', 'A@report_point', 'B@test_track'])
    expect(monitor.report()).toEqual([])
  })

  it('sends a second rework car to the next free stand of the bay', () => {
    const ds = new ScriptedDrivingSystem()
    const done: string[] = []
    ds.on('mission.done', (event) => done.push(`${event.vin}@${event.location}`))
    ds.spawn('HELD', { at: 'rework.electrical', colour: blue })
    ds.spawn('LAMP', { at: 'test_track', colour: blue })
    ds.send({ op: 'mission.go', mission_id: 'M1', vin: 'LAMP', waypoints: ['rework.electrical'] })
    const monitor = tickWatching(ds, ['HELD', 'LAMP'], 60)
    expect(done).toEqual(['LAMP@rework.electrical'])
    expect(ds.getPositionXY('LAMP')).toMatchObject({ x: POINTS['rework.electrical'].x + 3.5 })
    ds.send({ op: 'mission.go', mission_id: 'M2', vin: 'LAMP', waypoints: ['test_track'] })
    monitor.episodes.push(...tickWatching(ds, ['HELD', 'LAMP'], 80).episodes)
    expect(done).toEqual(['LAMP@rework.electrical', 'LAMP@test_track'])
    // Re-joins the lane before the pad and enters it the way lane traffic does.
    expect(ds.getPositionXY('LAMP')).toEqual({ ...POINTS.test_track })
    expect(monitor.report()).toEqual([])
  })

  it('moves a yard car along the yard lane past the cars parked in between', () => {
    const ds = new ScriptedDrivingSystem()
    for (const slot of ['yard.Y2', 'yard.Y3', 'yard.Y4', 'yard.Y5']) {
      ds.spawn(slot, { at: slot, colour: blue })
    }
    ds.send({ op: 'mission.go', mission_id: 'M1', vin: 'yard.Y5', waypoints: ['yard.Y1'] })
    const monitor = tickWatching(ds, ['yard.Y2', 'yard.Y3', 'yard.Y4', 'yard.Y5'], 40)
    expect(ds.getPositionXY('yard.Y5')).toEqual({ ...POINTS['yard.Y1'] })
    expect(monitor.report()).toEqual([])
  })

  it('lets a car queued behind a car parked at its destination arrive in its queue spot', () => {
    const ds = new ScriptedDrivingSystem()
    const done: string[] = []
    ds.on('mission.done', (event) => done.push(`${event.vin}@${event.location}`))
    ds.spawn('HELD', { at: 'report_point', colour: blue })
    ds.spawn('NEXT', { at: 'test_track', colour: blue })
    ds.send({ op: 'mission.go', mission_id: 'M1', vin: 'NEXT', waypoints: ['report_point'] })
    const monitor = tickWatching(ds, ['HELD', 'NEXT'], 20)
    expect(done).toEqual([])
    monitor.episodes.push(...tickWatching(ds, ['HELD', 'NEXT'], IDLE_QUEUE_S).episodes)
    expect(done).toEqual(['NEXT@report_point'])
    expect(ds.getPositionXY('NEXT').x).toBeLessThan(POINTS.report_point.x - CAR_LENGTH_M - 5.9)
    expect(monitor.report()).toEqual([])
  })

  it('untangles two cars meeting head-on (one pulls aside)', () => {
    const ds = new ScriptedDrivingSystem()
    const done: string[] = []
    ds.on('mission.done', (event) => done.push(`${event.vin}@${event.location}`))
    ds.spawn('EAST', { at: 'test_track', colour: blue })
    ds.spawn('WEST', { at: 'report_point', colour: blue })
    ds.send({ op: 'mission.go', mission_id: 'M1', vin: 'EAST', waypoints: ['report_point'] })
    ds.send({ op: 'mission.go', mission_id: 'M2', vin: 'WEST', waypoints: ['inspection_gate'] })
    const monitor = tickWatching(ds, ['EAST', 'WEST'], 120)
    expect(done.sort()).toEqual(['EAST@report_point', 'WEST@inspection_gate'])
    expect(monitor.overlaps()).toEqual([])
  })
})

describe('scripted engine traffic', () => {
  it('plays the default shift with no overlapping cars', async () => {
    const { monitor, shift } = await runStoreShift(DEFAULT_SCENARIO, 0)
    expect(shift.progress).toBe(1)
    expectNoOverlap(monitor)
    expect(shift.cars.P001).toMatchObject({ stage: 'parked', slot: 'A7' })
  }, 60_000)

  it('plays the default shift with slow AI answers and no overlapping cars', async () => {
    const { monitor, shift } = await runStoreShift(DEFAULT_SCENARIO, 3)
    expect(shift.progress).toBe(1)
    expectNoOverlap(monitor)
  }, 60_000)

  it('queues a five-car shift (with a headlight fault) without overlaps', async () => {
    const cars = ['blue', 'white', 'silver', 'black', 'red'].map((colour, i) => ({
      vin: `Q${i + 1}`,
      colour,
    }))
    const { monitor, orch, ds } = await runOrchestrator(cars, {
      latency_s: 2,
      script: (engine, _state, time) => {
        if (time === 0) engine.setFault('Q3', new Set(['headlights']))
      },
    })
    expect(orch.getState().progress).toBe(1)
    expectNoOverlap(monitor)
    // Waiting behind another car is queuing, not an assist request.
    expect(ds.commands.filter((cmd) => cmd.op === 'assist.choose')).toEqual([])
    expect(Object.keys(orch.getState().cases)).toEqual([])
  }, 60_000)

  it('keeps followers behind a car stopped by a blocker', async () => {
    const cars = ['blue', 'white', 'silver', 'black'].map((colour, i) => ({
      vin: `B${i + 1}`,
      colour,
    }))
    let raised = false
    const { monitor, orch, ds } = await runOrchestrator(cars, {
      script: (engine, _state, time) => {
        if (!raised && time >= 5) {
          raised = true
          const at = engine.getPositionXY('B1')
          engine.spawnProp('box-1', 'box', at.x + 6, at.y, 0)
          engine.raiseAssist('B1', 'AC-B1-1', 'Box in the lane', ['wait_for_removal'])
        }
        if (time >= 25) engine.removeProp('box-1')
      },
    })
    expect(orch.getState().progress).toBe(1)
    expectNoOverlap(monitor)
    expect(Object.keys(orch.getState().cases)).toEqual(['AC-B1-1'])
    expect(ds.assistOutcomes['AC-B1-1']).toMatchObject({ option: 'wait_for_removal' })
  }, 60_000)

  it('Scene A and E cars and held-at-report cars never overlap', async () => {
    const row = ['A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8']
    const preParked = row.map((slot) => ({ vin: `Y-${slot}`, colour: 'green', parkedAt: slot }))
    const held = await runOrchestrator(
      [...preParked, { vin: 'H1', colour: 'blue' }, { vin: 'H2', colour: 'blue' }],
      { trucks: [{ id: 'T-1', state: 'loading', slots: ['A1'] }] },
    )
    expect(held.orch.getState().cars.H2.stage).toBe('held')
    expectNoOverlap(held.monitor)

    const sceneA = await runOrchestrator([
      { vin: 'VIN-B1', colour: 'blue' },
      { vin: 'VIN-R1', colour: 'red', expectedColour: 'blue' },
      { vin: 'VIN-R2', colour: 'red', expectedColour: 'blue' },
      { vin: 'VIN-B2', colour: 'blue' },
    ])
    expect(sceneA.orch.getState().progress).toBe(1)
    expectNoOverlap(sceneA.monitor)

    const sceneE = await runOrchestrator(
      [
        { vin: 'VIN-Y8', colour: 'white', parkedAt: 'A8' },
        { vin: 'VIN-E', colour: 'blue', targetSlot: 'A7' },
        { vin: 'VIN-F', colour: 'blue' },
      ],
      { latency_s: 2 },
    )
    expect(sceneE.orch.getState().layout).toMatchObject({ A7: 'VIN-E', A8: 'VIN-Y8' })
    expect(sceneE.orch.getState().progress).toBe(1)
    expectNoOverlap(sceneE.monitor)
  }, 60_000)
})
