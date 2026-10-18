import { afterEach, describe, expect, it, vi } from 'vitest'
import { stageHazard } from '../hazards'
import { POINTS } from '../points'
import type { AssistChooseCmd } from '../types'
import { flush, runUntil, setup } from './harness'

const SLOTS_A1_TO_A6 = ['A1', 'A2', 'A3', 'A4', 'A5', 'A6']

afterEach(() => {
  vi.restoreAllMocks()
})

describe('orchestrator happy path', () => {
  it('drives a good car from the line end into a yard slot and logs KPIs', async () => {
    const fetchSpy = vi.fn()
    const realFetch = globalThis.fetch
    globalThis.fetch = fetchSpy as unknown as typeof fetch
    try {
      const { ds, orch, notes, missionsOf, feedText } = setup()
      orch.start([{ vin: 'VIN-OK', colour: 'blue' }])

      const first = missionsOf('VIN-OK')[0]
      expect(first.waypoints).toEqual(['buffer_zone_b', 'inspection_gate'])
      expect(first.max_speed_kmh).toBe(10)

      const parked = await runUntil(ds, orch.getState, (s) => s.cars['VIN-OK'].stage === 'parked')
      expect(parked).toBe(true)

      const state = orch.getState()
      expect(state.cars['VIN-OK'].slot).toBe('A1')
      expect(state.layout.A1).toBe('VIN-OK')
      expect(state.kpis).toMatchObject({ offLine: 1, inYard: 1, rework: 0, neededYou: 0 })
      expect(state.kpis.decisions).toBe(state.feed.length)
      expect(state.progress).toBe(1)

      const feed = feedText('VIN-OK')
      expect(feed.some((line) => line.startsWith('GEMINI Gate PASS: blue'))).toBe(true)
      const has = (pattern: RegExp) => feed.some((line) => pattern.test(line))
      expect(has(/GEMINI R-12 step 1 headlights: .* -> PASS/)).toBe(true)
      expect(has(/GEMINI R-12 step 3 brake_lights: .* -> PASS/)).toBe(true)
      expect(feed.some((line) => line.startsWith('GEMINI Route via finishing_corner'))).toBe(true)
      expect(feed.at(-1)).toBe('PLANT Parked in A1 (Y1)')

      const waypoints = missionsOf('VIN-OK').map((cmd) => cmd.waypoints.join('>'))
      expect(waypoints).toEqual([
        'buffer_zone_b>inspection_gate',
        'test_track',
        'report_point',
        'finishing>yard.entry',
      ])
      expect(missionsOf('VIN-OK')[1].routine).toBe('R-12')
      const parkingNote = /^GEMINI · VIN-OK parking Y1: path [ABC] \(\d\.\d\d\)$/
      expect(notes.some((n) => parkingNote.test(n.text))).toBe(true)
      expect(fetchSpy).not.toHaveBeenCalled()
    } finally {
      globalThis.fetch = realFetch
    }
  })

  it('mirrors engine events as sim:* and commands as marshal:command', async () => {
    const { ds, bus, orch, commands } = setup()
    const seen: string[] = []
    bus.on('sim:status', () => seen.push('status'))
    bus.on('sim:mission.done', () => seen.push('done'))
    orch.start([{ vin: 'VIN-1', colour: 'blue' }])
    expect(commands).toHaveLength(1)
    await runUntil(ds, orch.getState, (s) => s.cars['VIN-1'].stage === 'test_track')
    expect(seen).toContain('status')
    expect(seen).toContain('done')
    expect(ds.commands).toEqual(commands)
  })

  it('queues cars 9m apart behind line_end', () => {
    const { ds, orch } = setup()
    orch.start([
      { vin: 'VIN-1', colour: 'blue' },
      { vin: 'VIN-2', colour: 'blue' },
    ])
    expect(ds.getPositionXY('VIN-1').x).toBeCloseTo(POINTS.line_end.x)
    expect(ds.getPositionXY('VIN-2').x).toBeCloseTo(POINTS.line_end.x - 9)
  })
})

describe('fail-closed offline checks and shift completion', () => {
  it('holds a car for inspection when the offline gate has no evidence', async () => {
    const { ds, orch, feedText } = setup({ hints: false })
    orch.start([{ vin: 'VIN-X', colour: 'blue' }])
    const done = await runUntil(
      ds,
      orch.getState,
      (s) => s.cars['VIN-X'].location === 'rework.electrical',
    )
    expect(done).toBe(true)
    expect(orch.getState().progress).toBe(1)
    const car = orch.getState().cars['VIN-X']
    expect(car).toMatchObject({ stage: 'rework', reworkReason: 'inspection' })
    const feed = feedText('VIN-X')
    const fail = 'GEMINI Gate FAIL: not_verified_offline; saw unknown, built blue (0.00)'
    expect(feed).toContain(fail)
    expect(feed.at(-1)).toMatch(/AI check unavailable/)
  })

  it('holds a car for inspection when the offline lamp check has no evidence', async () => {
    const { ds, orch, missionsOf } = setup({
      hints: false,
      ai: {
        evaluateGatePhotos: async () => ({
          pass: true,
          colour: 'blue',
          confidence: 0.9,
          faults: [],
          degraded: false,
        }),
      },
    })
    orch.start([{ vin: 'VIN-L', colour: 'blue' }])
    const done = await runUntil(ds, orch.getState, (s) => s.progress === 1)
    expect(done).toBe(true)
    expect(orch.getState().cars['VIN-L']).toMatchObject({ reworkReason: 'inspection' })
    // Not repaired and re-run in a loop: the car stays in the bay.
    const tracks = missionsOf('VIN-L').filter((cmd) => cmd.waypoints.includes('test_track'))
    expect(tracks).toHaveLength(1)
  })

  it('completes the shift when a car is held because the yard is full', async () => {
    const { ds, orch, feedText } = setup()
    orch.start([{ vin: 'VIN-F', colour: 'blue' }], {
      reservedSlots: ['A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8'],
    })
    const done = await runUntil(ds, orch.getState, (s) => s.progress === 1)
    expect(done).toBe(true)
    expect(orch.getState().cars['VIN-F']).toMatchObject({ stage: 'held' })
    expect(orch.getState().cars['VIN-F'].held).toMatch(/No free yard slot/)
    expect(feedText('VIN-F').at(-1)).toMatch(/^YARD No free yard slot/)
  })
})

describe('Scene A: route choice and gate failure', () => {
  it('routes via lane_c around a staged hazard; wrong colour goes to rework', async () => {
    const { ds, orch, missionsOf, feedText } = setup()
    const hazard = stageHazard(ds, 'finishing_corner', 'forklift_unloading')
    orch.setHazard(hazard.description)
    orch.start([
      { vin: 'VIN-B1', colour: 'blue' },
      { vin: 'VIN-R1', colour: 'red', expectedColour: 'blue' },
    ])

    const done = await runUntil(
      ds,
      orch.getState,
      (s) =>
        s.cars['VIN-B1'].stage === 'parked' && s.cars['VIN-R1'].location === 'rework.electrical',
    )
    expect(done).toBe(true)

    const yardMission = missionsOf('VIN-B1').at(-1)
    expect(yardMission?.waypoints).toEqual(['lane_c', 'yard.entry'])
    expect(feedText('VIN-B1').some((line) => line.startsWith('GEMINI Route via lane_c'))).toBe(true)

    const red = orch.getState().cars['VIN-R1']
    expect(red.stage).toBe('rework')
    expect(red.reworkReason).toBe('gate')
    expect(missionsOf('VIN-R1').at(-1)?.waypoints).toEqual(['rework.electrical'])
    const gateFail = 'GEMINI Gate FAIL: colour_mismatch'
    expect(feedText('VIN-R1').some((line) => line.startsWith(gateFail))).toBe(true)
    expect(orch.getState().kpis).toMatchObject({ offLine: 2, inYard: 1, rework: 1 })
    expect(orch.getState().progress).toBe(1)
  })
})

describe('Scene B: headlight fault', () => {
  it('fails headlights, goes to rework, is repaired, re-checks step 1 and passes', async () => {
    const { ds, orch, missionsOf, feedText } = setup()
    orch.start([{ vin: 'VIN-DARK', colour: 'blue' }])
    ds.setFault('VIN-DARK', new Set(['headlights']))

    const parked = await runUntil(ds, orch.getState, (s) => s.cars['VIN-DARK'].stage === 'parked')
    expect(parked).toBe(true)

    const feed = feedText('VIN-DARK')
    const fail = feed.findIndex((line) => line.includes('headlights: telemetry ON, camera OFF'))
    const repaired = feed.findIndex((line) => line.startsWith('PLANT Rework repaired headlights'))
    const recheck = feed.findIndex(
      (line, index) => index > repaired && /step 1 headlights: .* -> PASS/.test(line),
    )
    expect(fail).toBeGreaterThanOrEqual(0)
    expect(feed[fail]).toContain('-> FAIL')
    expect(repaired).toBeGreaterThan(fail)
    expect(recheck).toBeGreaterThan(repaired)

    const missions = missionsOf('VIN-DARK')
    expect(missions[2].waypoints).toEqual(['rework.electrical'])
    expect(missions[3]).toMatchObject({
      waypoints: ['test_track'],
      routine: 'R-12',
      routine_steps: [1],
    })
    expect(missions[4].waypoints).toEqual(['report_point'])
    expect(ds.faultsOf('VIN-DARK')).toEqual([])
    expect(orch.getState().kpis.rework).toBe(1)
  })
})

describe('Scene C: assist request', () => {
  function block(ds: ReturnType<typeof setup>['ds'], vin: string, withWorker: boolean) {
    const at = ds.getPositionXY(vin)
    ds.spawnProp('box-1', 'box', at.x + 6, at.y, 0)
    if (withWorker) ds.spawnProp('worker-1', 'worker', at.x + 7, at.y + 1, 90)
    ds.raiseAssist(vin, `AC-${vin}-1`, 'Box and worker blocking the lane', [
      'wait_for_removal',
      'pass_left_3kmh',
    ])
  }

  it('waits for the supervisor when Pause when needed is on', async () => {
    const { ds, orch, commands } = setup({ pauseWhenNeeded: true })
    orch.start([{ vin: 'VIN-STUCK', colour: 'blue' }])
    ds.tick(1)
    block(ds, 'VIN-STUCK', true)
    await flush()

    const state = orch.getState()
    const item = state.cases['AC-VIN-STUCK-1']
    expect(item.status).toBe('open')
    expect(item.frames).toHaveLength(8)
    expect(item.analysis?.recommendation).toBe('wait_for_removal')
    expect(state.cars['VIN-STUCK'].stage).toBe('waiting')
    expect(state.kpis.neededYou).toBe(1)
    expect(state.paused).toBe(true)
    expect(commands.some((cmd) => cmd.op === 'assist.choose')).toBe(false)

    expect(orch.chooseAssist('AC-VIN-STUCK-1', 'reroute_lane_c')).toBe(false)
    expect(orch.chooseAssist('AC-VIN-STUCK-1', 'wait_for_removal')).toBe(true)
    expect(orch.chooseAssist('AC-VIN-STUCK-1', 'wait_for_removal')).toBe(false)
    const choose = commands.filter((cmd): cmd is AssistChooseCmd => cmd.op === 'assist.choose')
    expect(choose).toEqual([
      {
        op: 'assist.choose',
        case_id: 'AC-VIN-STUCK-1',
        vin: 'VIN-STUCK',
        option: 'wait_for_removal',
        chosen_by: 'supervisor',
      },
    ])
    expect(orch.getState().cars['VIN-STUCK'].stage).toBe('to_gate')
    expect(orch.getState().paused).toBe(false)

    const stoppedAt = ds.getPositionXY('VIN-STUCK').x
    ds.tick(3)
    expect(ds.getPositionXY('VIN-STUCK').x).toBe(stoppedAt)
    ds.removeProp('box-1')
    ds.removeProp('worker-1')
    ds.tick(1)
    ds.tick(1)
    expect(ds.getPositionXY('VIN-STUCK').x).toBeGreaterThan(stoppedAt)
    expect(ds.assistOutcomes['AC-VIN-STUCK-1']).toMatchObject({ option: 'wait_for_removal' })
    const reached = await runUntil(ds, orch.getState, (s) => s.cars['VIN-STUCK'].stage === 'parked')
    expect(reached).toBe(true)
  })

  it('auto-applies the Gemini recommendation when Pause when needed is off', async () => {
    const { ds, orch, commands, feedText } = setup({ pauseWhenNeeded: false })
    orch.start([{ vin: 'VIN-STUCK', colour: 'blue' }])
    ds.tick(1)
    block(ds, 'VIN-STUCK', false)
    await flush()

    const choose = commands.filter((cmd): cmd is AssistChooseCmd => cmd.op === 'assist.choose')
    expect(choose).toHaveLength(1)
    expect(choose[0]).toMatchObject({ option: 'pass_left_3kmh', chosen_by: 'gemini' })
    expect(orch.getState().cases['AC-VIN-STUCK-1'].status).toBe('decided')
    expect(orch.getState().paused).toBe(false)
    expect(feedText('VIN-STUCK').at(-1)).toBe('GEMINI AC-VIN-STUCK-1: chose pass_left_3kmh')
  })

  it('applies pending recommendations when Pause when needed is switched off', async () => {
    const { ds, orch, commands } = setup({ pauseWhenNeeded: true })
    orch.start([{ vin: 'VIN-STUCK', colour: 'blue' }])
    ds.tick(1)
    block(ds, 'VIN-STUCK', true)
    await flush()
    orch.setPauseWhenNeeded(false)
    const choose = commands.filter((cmd): cmd is AssistChooseCmd => cmd.op === 'assist.choose')
    expect(choose).toHaveLength(1)
    expect(choose[0]).toMatchObject({ option: 'wait_for_removal', chosen_by: 'gemini' })
  })
})

describe('Scene E: parking', () => {
  it('parks in Y7 behind a car parked in Y8', async () => {
    const { ds, orch, notes } = setup()
    orch.start(
      [
        { vin: 'VIN-Y8', colour: 'blue', parkedAt: 'A8' },
        { vin: 'VIN-P1', colour: 'blue' },
      ],
      { reservedSlots: SLOTS_A1_TO_A6 },
    )
    const parked = await runUntil(ds, orch.getState, (s) => s.cars['VIN-P1'].stage === 'parked')
    expect(parked).toBe(true)

    const state = orch.getState()
    expect(state.cars['VIN-P1'].slot).toBe('A7')
    expect(state.layout).toMatchObject({ A7: 'VIN-P1', A8: 'VIN-Y8' })
    expect(state.kpis.inYard).toBe(2)
    const parkingNotes = notes.filter((n) => n.text.startsWith('GEMINI · VIN-P1 parking Y7'))
    expect(parkingNotes.length).toBeGreaterThanOrEqual(2)
    expect(parkingNotes[0].text).toMatch(/path [ABC] \(\d\.\d\d\)$/)
    expect(parkingNotes.at(-1)?.text).toBe('GEMINI · VIN-P1 parking Y7: parked (1.00)')
    expect(notes.at(-1)).toEqual({ text: 'PLANT · VIN-P1 parked in Y7', kind: 'plant' })
    expect(ds.parkingLog['VIN-P1'].length).toBeGreaterThan(0)
  })

  it('raises an assist on ask_person, then parks once the slot clears', async () => {
    const { ds, orch, feedText } = setup()
    ds.spawnProp('crate', 'box', POINTS['yard.Y1'].x, POINTS['yard.Y1'].y, 0)
    orch.start([{ vin: 'VIN-X', colour: 'blue' }])

    const waiting = await runUntil(ds, orch.getState, (s) => s.cases['AC-VIN-X-P1'] !== undefined)
    expect(waiting).toBe(true)
    const state = orch.getState()
    expect(state.cases['AC-VIN-X-P1']).toMatchObject({
      vin: 'VIN-X',
      options: ['wait_for_removal'],
      status: 'open',
    })
    expect(state.cars['VIN-X'].stage).toBe('waiting')
    expect(feedText('VIN-X')).toContain('GEMINI VIN-X parking Y1: ask a person (0.00)')

    ds.removeProp('crate')
    expect(orch.chooseAssist('AC-VIN-X-P1', 'wait_for_removal')).toBe(true)
    const parked = await runUntil(ds, orch.getState, (s) => s.cars['VIN-X'].stage === 'parked')
    expect(parked).toBe(true)
    expect(orch.getState().layout.A1).toBe('VIN-X')
  })
})

describe('held cars', () => {
  const FULL_ROW = ['A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8']
  const preParked = FULL_ROW.map((slot) => ({ vin: `Y-${slot}`, colour: 'green', parkedAt: slot }))

  it('holds a car for the yard crew (not for you) when parking is abandoned', async () => {
    const { ds, orch, feedText } = setup()
    ds.spawnProp('crate', 'box', POINTS['yard.Y1'].x, POINTS['yard.Y1'].y, 0)
    orch.start([{ vin: 'VIN-X', colour: 'blue' }])
    await runUntil(ds, orch.getState, (s) => s.cases['AC-VIN-X-P1'] !== undefined)
    // The crate stays: the second attempt gives up and the yard crew takes over.
    expect(orch.chooseAssist('AC-VIN-X-P1', 'wait_for_removal')).toBe(true)
    const held = await runUntil(ds, orch.getState, (s) => s.cars['VIN-X'].stage === 'held')
    expect(held).toBe(true)

    const state = orch.getState()
    expect(state.cars['VIN-X']).toMatchObject({ stage: 'held', slot: 'A1' })
    expect(state.cars['VIN-X'].held).toMatch(/abandoned after 2 attempts/)
    expect(Object.values(state.cases).filter((item) => item.status === 'open')).toEqual([])
    expect(state.paused).toBe(false)
    expect(state.progress).toBe(1)
    expect(state.kpis.inYard).toBe(0)
    expect(feedText('VIN-X').at(-1)).toMatch(/^YARD Parking Y1 abandoned .* the yard crew/)
  })

  it('offers a held-at-report car the slot freed when a truck leaves', async () => {
    const { ds, orch, feedText } = setup()
    orch.start([...preParked, { vin: 'VIN-H', colour: 'blue' }], {
      trucks: [{ id: 'T-1', state: 'loading', slots: ['A1'] }],
    })
    const held = await runUntil(ds, orch.getState, (s) => s.cars['VIN-H'].stage === 'held')
    expect(held).toBe(true)
    expect(orch.getState().progress).toBe(1)

    expect(orch.shipTruck('T-1')).toEqual(['Y-A1'])
    expect(orch.getState().cars['Y-A1']).toMatchObject({ stage: 'shipped' })
    expect(orch.getState().cars['Y-A1'].slot).toBeUndefined()
    expect(orch.getState().trucks[0].state).toBe('left')
    expect(orch.getState().kpis.shipped).toBe(1)
    expect(orch.shipTruck('T-1')).toEqual([])

    const parked = await runUntil(ds, orch.getState, (s) => s.cars['VIN-H'].stage === 'parked')
    expect(parked).toBe(true)
    expect(orch.getState().cars['VIN-H']).toMatchObject({ slot: 'A1' })
    expect(orch.getState().cars['VIN-H'].held).toBeUndefined()
    expect(orch.getState().layout.A1).toBe('VIN-H')
    const feed = feedText('VIN-H')
    const retry = feed.indexOf('YARD A yard slot freed; retrying slot assignment')
    expect(retry).toBeGreaterThan(feed.findIndex((line) => line.startsWith('YARD No free yard')))
    expect(feed[retry + 2]).toBe('YARD Assigned slot A1 (yard.Y1)')
    expect(feedText('Y-A1')).toContain('PLANT Shipped on T-1 from A1')
  })

  it('offers a held-at-report car the slot freed by a yard re-plan move', async () => {
    const { ds, orch } = setup({
      ai: {
        replanYardSlots: async () => ({
          moves: [{ vin: 'Y-A1', from: 'A1', to: 'B1' }],
          reason: 'Make room',
          degraded: false,
        }),
      },
    })
    orch.start([...preParked, { vin: 'VIN-H', colour: 'blue' }])
    await runUntil(ds, orch.getState, (s) => s.cars['VIN-H'].stage === 'held')
    const outcome = await orch.applyYardReplan({ reason: 'Make room' })
    expect(outcome.ordered).toHaveLength(1)
    expect(orch.getState().layout).toMatchObject({ A1: null, B1: 'Y-A1' })
    const parked = await runUntil(ds, orch.getState, (s) => s.cars['VIN-H'].stage === 'parked')
    expect(parked).toBe(true)
    expect(orch.getState().layout.A1).toBe('VIN-H')
  })

  it('retries one held car per freed slot', async () => {
    const { ds, orch, feedText } = setup()
    orch.start(
      [...preParked, { vin: 'VIN-H1', colour: 'blue' }, { vin: 'VIN-H2', colour: 'blue' }],
      { trucks: [{ id: 'T-1', state: 'loading', slots: ['A1'] }] },
    )
    await runUntil(
      ds,
      orch.getState,
      (s) => s.cars['VIN-H1'].stage === 'held' && s.cars['VIN-H2'].stage === 'held',
    )
    orch.shipTruck('T-1')
    await runUntil(ds, orch.getState, (s) => s.cars['VIN-H1'].stage === 'parked')
    const state = orch.getState()
    expect(state.cars['VIN-H1']).toMatchObject({ stage: 'parked', slot: 'A1' })
    expect(state.cars['VIN-H2']).toMatchObject({ stage: 'held' })
    expect(feedText('VIN-H2').filter((line) => line.startsWith('YARD No free yard'))).toHaveLength(
      1,
    )
    expect(feedText('VIN-H2')).not.toContain('YARD A yard slot freed; retrying slot assignment')
    expect(state.progress).toBe(1)
  })
})

describe('Scene E in the demo slot naming', () => {
  it('parks in the requested slot A7 (yard.Y7) behind a car parked in A8 (yard.Y8)', async () => {
    const { ds, orch, feedText } = setup()
    orch.start([
      { vin: 'VIN-Y8', colour: 'white', parkedAt: 'A8' },
      { vin: 'VIN-E', colour: 'blue', targetSlot: 'A7' },
    ])
    const parked = await runUntil(ds, orch.getState, (s) => s.cars['VIN-E'].stage === 'parked')
    expect(parked).toBe(true)
    expect(orch.getState().layout).toMatchObject({ A7: 'VIN-E', A8: 'VIN-Y8' })
    const feed = feedText('VIN-E')
    expect(feed).toContain('YARD Assigned slot A7 (yard.Y7)')
    expect(feed.some((line) => /^GEMINI VIN-E parking Y7: path [ABC]/.test(line))).toBe(true)
    expect(feed.at(-1)).toBe('PLANT Parked in A7 (Y7)')
  })

  it('falls back to the usual slot choice when the requested slot is taken', async () => {
    const { ds, orch } = setup()
    orch.start([
      { vin: 'VIN-Y7', colour: 'white', parkedAt: 'A7' },
      { vin: 'VIN-E', colour: 'blue', targetSlot: 'A7' },
    ])
    const parked = await runUntil(ds, orch.getState, (s) => s.cars['VIN-E'].stage === 'parked')
    expect(parked).toBe(true)
    expect(orch.getState().cars['VIN-E'].slot).toBe('A1')
  })
})
