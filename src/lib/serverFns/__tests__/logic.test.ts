import { describe, expect, it } from 'vitest'
import { planCandidatePaths } from '#/lib/marshal/parking/astar'
import { POINTS } from '#/lib/marshal/points'
import type {
  AssistInput,
  GateInput,
  LampInput,
  ParkingInput,
  RouteInput,
  YardInput,
} from '#/lib/marshal/schemas'
import type { AssistOption, RouteOption } from '#/lib/marshal/types'
import {
  enforceAssist,
  enforceGate,
  enforceLamp,
  enforceParking,
  enforceRoute,
  enforceYard,
  offlineAssist,
  offlineGate,
  offlineLamp,
  offlineParking,
  offlineRoute,
  offlineYard,
  parseHint,
  UNVERIFIED,
  UNVERIFIED_FAULT,
  validYardMoves,
} from '../logic'

const frames = { front: 'A', rear: 'A', left: 'A', right: 'A' }
const gate = (hints?: GateInput['hints']): GateInput => ({
  vin: 'V',
  expectedColour: 'Blue',
  frames,
  hints,
})

describe('parseHint', () => {
  it('parses key=value pairs case-insensitively', () => {
    expect(parseHint('Colour=Red; headlights=off, workers=0')).toEqual({
      colour: 'red',
      headlights: 'off',
      workers: '0',
    })
    expect(parseHint(undefined)).toEqual({})
  })
})

describe('gate', () => {
  it('passes offline when colour matches and there is no damage', () => {
    const out = offlineGate(gate({ front: 'colour=blue', left: 'damage=none' }))
    expect(out).toMatchObject({ pass: true, colour: 'blue', faults: [], degraded: true })
  })

  it('fails offline on colour mismatch or damage', () => {
    const mismatch = offlineGate(gate({ front: 'colour=red' }))
    expect(mismatch).toMatchObject({ pass: false, colour: 'red', degraded: true })
    expect(mismatch.faults).toContain('colour_mismatch')
    const damaged = offlineGate(gate({ left: 'colour=blue; damage=dent' }))
    expect(damaged.pass).toBe(false)
    expect(damaged.faults).toEqual(['damage:left:dent'])
  })

  it('fails closed offline when no view has a hint', () => {
    expect(offlineGate(gate())).toEqual({
      pass: false,
      colour: 'unknown',
      confidence: 0,
      faults: [UNVERIFIED_FAULT],
      degraded: true,
    })
  })

  it('never lets Gemini pass a colour mismatch', () => {
    const out = enforceGate(gate(), { pass: true, colour: 'red', confidence: 2, faults: [] })
    expect(out).toMatchObject({ pass: false, confidence: 1, degraded: false })
    expect(out.faults).toContain('colour_mismatch')
    const dented = enforceGate(gate(), {
      pass: true,
      colour: 'blue',
      confidence: 0.9,
      faults: ['dent'],
    })
    expect(dented.pass).toBe(false)
  })
})

describe('lamp', () => {
  const lamp = (telemetry: Record<string, unknown>, hint?: string): LampInput => ({
    vin: 'V',
    step: 'headlights',
    telemetry,
    frames: [],
    hint,
  })

  it('fails when telemetry says ON but the camera hint says off', () => {
    expect(offlineLamp(lamp({ headlights: 'ON' }, 'headlights=off'))).toEqual({
      expected: 'ON',
      observed: 'OFF',
      pass: false,
      degraded: true,
    })
  })

  it('passes when the observation matches telemetry', () => {
    expect(offlineLamp(lamp({ headlights: 'ON' }, 'headlights=on')).pass).toBe(true)
  })

  it('fails closed as UNVERIFIED when there is no hint', () => {
    expect(offlineLamp(lamp({ headlights: true }))).toEqual({
      expected: 'ON',
      observed: UNVERIFIED,
      pass: false,
      degraded: true,
    })
    expect(offlineLamp(lamp({ headlights: 'ON' }, 'headlights=maybe')).observed).toBe(UNVERIFIED)
  })

  it('takes expected from telemetry, not from Gemini', () => {
    const out = enforceLamp(lamp({ headlights: 'ON' }), { observed: 'OFF' })
    expect(out).toEqual({ expected: 'ON', observed: 'OFF', pass: false, degraded: false })
    const unreadable = enforceLamp(lamp({ headlights: 'ON' }, 'headlights=off'), { observed: '?' })
    expect(unreadable.degraded).toBe(true)
  })
})

describe('assist', () => {
  const all: AssistOption[] = ['wait_for_removal', 'pass_left_3kmh', 'reroute_lane_c']
  const assist = (options: AssistOption[], reason: string, hint?: string): AssistInput => ({
    case_id: 'c1',
    reason,
    options,
    frames: [],
    hint,
  })

  it('waits for a person only when waiting is offered', () => {
    expect(offlineAssist(assist(all, 'lane blocked', 'worker=1')).recommendation).toBe(
      'wait_for_removal',
    )
    expect(offlineAssist(assist(all, 'Worker in lane')).recommendation).toBe('wait_for_removal')
    expect(
      offlineAssist(assist(['pass_left_3kmh', 'reroute_lane_c'], 'x', 'worker=1')).recommendation,
    ).toBe('pass_left_3kmh')
  })

  it('passes left for objects, else picks the first offered option', () => {
    const box = offlineAssist(assist(all, 'box in lane', 'worker=0'))
    expect(box).toMatchObject({ recommendation: 'pass_left_3kmh', boxes: [], degraded: true })
    expect(box.description).toContain('box in lane')
    expect(offlineAssist(assist(['reroute_lane_c'], 'box')).recommendation).toBe('reroute_lane_c')
  })

  it('never recommends an option that was not offered', () => {
    const options: AssistOption[] = ['reroute_lane_c']
    const out = enforceAssist(assist(options, 'box'), {
      description: 'a box',
      boxes: [{ label: 'box', x: 0.9, y: -1, w: 0.5, h: 0.5 }],
      recommendation: 'pass_left_3kmh',
    })
    expect(out.recommendation).toBe('reroute_lane_c')
    expect(out.boxes).toEqual([{ label: 'box', x: 0.9, y: 0, w: expect.closeTo(0.1), h: 0.5 }])
    expect(out.degraded).toBe(false)
  })

  it('overrides passing a person when waiting is offered', () => {
    const out = enforceAssist(assist(all, 'lane blocked', 'worker=1'), {
      description: 'worker',
      boxes: [],
      recommendation: 'pass_left_3kmh',
    })
    expect(out.recommendation).toBe('wait_for_removal')
  })
})

describe('parking', () => {
  const slot = { id: 'yard.Y7', ...POINTS['yard.Y7'] }
  const input = (pose: ParkingInput['pose'], obstacles: ParkingInput['obstacles'] = []) => ({
    vin: 'V',
    pose,
    obstacles,
    slot,
  })
  const away = input({ x: slot.x + 1, y: slot.y - 3, yaw: 95 })

  it('reports parked when already in the slot', () => {
    expect(offlineParking(input({ x: slot.x, y: slot.y, yaw: 90 }))).toEqual({
      path: [],
      decision: 'parked',
      confidence: 1,
      degraded: true,
    })
  })

  it('picks the cheapest A* path offline', () => {
    const candidates = planCandidatePaths(away.pose, [], slot)
    const out = offlineParking(away, candidates)
    expect(out).toMatchObject({ decision: 'path_a', degraded: true })
    expect(out.path).toEqual(candidates[0].steps)
  })

  it('asks a person when no path exists', () => {
    const blocked = input(away.pose, [
      { id: 'crate', x: slot.x, y: slot.y, yaw: 0, length: 1, width: 1, moving: false },
    ])
    expect(offlineParking(blocked).decision).toBe('ask_person')
  })

  it('only accepts Gemini decisions that name an existing candidate', () => {
    const candidates = planCandidatePaths(away.pose, [], slot)
    const last = candidates.at(-1)
    if (!last) throw new Error('expected candidates')
    const decision = `path_${last.label.toLowerCase()}`
    const chosen = enforceParking(away, candidates, { decision, confidence: 0.8 })
    expect(chosen).toMatchObject({ decision, path: last.steps, degraded: false })
    const missing = enforceParking(away, candidates.slice(0, 1), {
      decision: 'path_c',
      confidence: 1,
    })
    expect(missing).toMatchObject({ decision: 'path_a', degraded: true })
    const notParked = enforceParking(away, candidates, { decision: 'parked', confidence: 1 })
    expect(notParked.decision).toBe('path_a')
    const ask = enforceParking(away, candidates, { decision: 'ask_person', confidence: 0.4 })
    expect(ask).toMatchObject({ decision: 'ask_person', path: [], degraded: false })
  })
})

describe('route', () => {
  const route = (hazardDescription: string, options: RouteOption[]): RouteInput => ({
    hazardDescription,
    options,
  })

  it('takes lane_c only for a described hazard when offered', () => {
    expect(offlineRoute(route('forklift at corner', ['finishing_corner', 'lane_c'])).route).toBe(
      'lane_c',
    )
    expect(offlineRoute(route('  ', ['finishing_corner', 'lane_c'])).route).toBe('finishing_corner')
    expect(offlineRoute(route('forklift', ['finishing_corner']))).toMatchObject({
      route: 'finishing_corner',
      degraded: true,
    })
    expect(offlineRoute(route('', ['lane_c'])).route).toBe('lane_c')
  })

  it('always returns a route within the options', () => {
    const input = route('', ['finishing_corner'])
    expect(enforceRoute(input, { route: 'lane_c', confidence: 1, reason: 'x' })).toMatchObject({
      route: 'finishing_corner',
      degraded: true,
    })
    const answer = { route: 'finishing_corner', confidence: 0.7, reason: 'ok' }
    const accepted = enforceRoute(input, answer)
    expect(accepted).toEqual({
      route: 'finishing_corner',
      confidence: 0.7,
      reason: 'ok',
      degraded: false,
    })
  })
})

describe('yard', () => {
  const yard: YardInput = {
    layout: { 'yard.Y1': null, 'yard.Y2': 'V2', 'yard.Y3': 'V3', 'yard.Y10': null },
    constraints: { priorityVins: ['V3'], reservedSlots: ['yard.Y2'], reason: 'late truck' },
  }

  it('vacates reserved slots then moves priority VINs forward deterministically', () => {
    const out = offlineYard(yard)
    // V2 leaves reserved Y2 for Y1; V3 stays because the only free slot (Y10) is further back.
    expect(out.moves).toEqual([{ vin: 'V2', from: 'yard.Y2', to: 'yard.Y1' }])
    expect(out.degraded).toBe(true)
    expect(out.reason).toContain('late truck')
    expect(validYardMoves(yard, out.moves)).toBe(true)
    expect(offlineYard(yard)).toEqual(out)
  })

  it('reports no moves when nothing needs to change', () => {
    const out = offlineYard({ layout: { 'yard.Y1': 'V1', 'yard.Y2': null }, constraints: {} })
    expect(out).toEqual({ moves: [], reason: 'No slot moves needed', degraded: true })
  })

  it('rejects illegal Gemini moves', () => {
    const illegal = [{ vin: 'V3', from: 'yard.Y3', to: 'yard.Y2' }]
    expect(validYardMoves(yard, illegal)).toBe(false)
    expect(enforceYard(yard, { moves: illegal, reason: 'x' })).toEqual(offlineYard(yard))
    const legal = [{ vin: 'V3', from: 'yard.Y3', to: 'yard.Y1' }]
    expect(enforceYard(yard, { moves: legal, reason: 'priority' })).toEqual({
      moves: legal,
      reason: 'priority',
      degraded: false,
    })
  })
})
