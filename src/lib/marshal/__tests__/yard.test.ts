import { describe, expect, it } from 'vitest'
import { orderMoves, validateReplan } from '../flows/yard'
import type { YardMove } from '../types'
import { runUntil, setup } from './harness'

const layout: Record<string, string | null> = {
  A1: 'P1',
  A2: 'P2',
  A3: null,
  A4: 'P4',
  B1: null,
}

const reasons = (moves: readonly YardMove[], reserved: string[] = []) =>
  validateReplan(layout, moves, reserved).rejected.map((item) => item.reason)

describe('validateReplan', () => {
  it('accepts a move into a free slot', () => {
    const move = { vin: 'P1', from: 'A1', to: 'A3' }
    expect(validateReplan(layout, [move])).toEqual({ accepted: [move], rejected: [] })
  })

  it('rejects occupied, reserved and non-existent targets', () => {
    expect(reasons([{ vin: 'P1', from: 'A1', to: 'A4' }])).toEqual(['target A4 is occupied by P4'])
    expect(reasons([{ vin: 'P1', from: 'A1', to: 'A3' }], ['A3'])).toEqual([
      'target A3 is reserved',
    ])
    expect(reasons([{ vin: 'P1', from: 'A1', to: 'Z9' }])).toEqual(['target Z9 does not exist'])
  })

  it('rejects a VIN that is not in its from slot, no-op moves and duplicates', () => {
    expect(reasons([{ vin: 'P2', from: 'A1', to: 'A3' }])).toEqual(['P2 is not in A1'])
    expect(reasons([{ vin: 'P1', from: 'A1', to: 'A1' }])).toEqual(['move does not change slot'])
    expect(
      reasons([
        { vin: 'P1', from: 'A1', to: 'A3' },
        { vin: 'P1', from: 'A1', to: 'B1' },
      ]),
    ).toEqual(['P1 is moved twice'])
    expect(
      reasons([
        { vin: 'P1', from: 'A1', to: 'A3' },
        { vin: 'P2', from: 'A2', to: 'A3' },
      ]),
    ).toEqual(['target A3 is used twice'])
  })

  it('accepts a target vacated by another move and cascades rejections', () => {
    const chain = [
      { vin: 'P2', from: 'A2', to: 'A1' },
      { vin: 'P1', from: 'A1', to: 'A3' },
    ]
    expect(validateReplan(layout, chain).accepted).toEqual(chain)

    const stranded = [
      { vin: 'P2', from: 'A2', to: 'A1' },
      { vin: 'P1', from: 'A1', to: 'A4' },
    ]
    const result = validateReplan(layout, stranded)
    expect(result.accepted).toEqual([])
    expect(result.rejected.map((item) => item.move.vin)).toEqual(['P1', 'P2'])
  })
})

describe('orderMoves', () => {
  it('makes P2 wait for P1 when P1 still holds its target', () => {
    const { ordered, rejected } = orderMoves(layout, [
      { vin: 'P2', from: 'A2', to: 'A1' },
      { vin: 'P1', from: 'A1', to: 'A3' },
    ])
    expect(rejected).toEqual([])
    expect(ordered).toEqual([
      { vin: 'P1', from: 'A1', to: 'A3' },
      { vin: 'P2', from: 'A2', to: 'A1', waitsFor: 'P1' },
    ])
  })

  it('rejects a swap that has no free slot', () => {
    const swap = [
      { vin: 'P1', from: 'A1', to: 'A2' },
      { vin: 'P2', from: 'A2', to: 'A1' },
    ]
    expect(validateReplan(layout, swap).accepted).toEqual(swap)
    const { ordered, rejected } = orderMoves(layout, swap)
    expect(ordered).toEqual([])
    expect(rejected.map((item) => item.reason)).toEqual([
      'circular move needs a free slot',
      'circular move needs a free slot',
    ])
  })
})

describe('Scene D: late truck yard re-plan', () => {
  it('validates the plan, logs rejected moves and dispatches P1 before P2', async () => {
    const proposed: YardMove[] = [
      { vin: 'P2', from: 'A2', to: 'A1' },
      { vin: 'P1', from: 'A1', to: 'A3' },
      { vin: 'P3', from: 'A5', to: 'A6' },
      { vin: 'P3', from: 'A5', to: 'Z9' },
      { vin: 'P3', from: 'A5', to: 'A4' },
    ]
    const { ds, orch, missionsOf, feedText } = setup({
      ai: {
        replanYardSlots: async () => ({ moves: proposed, reason: 'T-1 late', degraded: false }),
      },
    })
    orch.start(
      [
        { vin: 'P1', colour: 'blue', parkedAt: 'A1' },
        { vin: 'P2', colour: 'blue', parkedAt: 'A2' },
        { vin: 'P4', colour: 'blue', parkedAt: 'A4' },
        { vin: 'P3', colour: 'blue', parkedAt: 'A5' },
      ],
      { trucks: [{ id: 'T-1', state: 'truck', slots: ['A1', 'A2'] }] },
    )

    const outcome = await orch.applyYardReplan(
      { reservedSlots: ['A6'], reason: 'Late truck T-1' },
      { truck: { id: 'T-1', state: 'waiting' } },
    )
    expect(outcome.ordered).toEqual([
      { vin: 'P1', from: 'A1', to: 'A3' },
      { vin: 'P2', from: 'A2', to: 'A1', waitsFor: 'P1' },
    ])
    expect(outcome.rejected.map((item) => item.reason)).toEqual([
      'target A6 is reserved',
      'target Z9 does not exist',
      'target A4 is occupied by P4',
    ])
    expect(orch.getState().trucks[0].state).toBe('waiting')
    expect(missionsOf('P1').map((cmd) => cmd.waypoints)).toEqual([['yard.Y3']])
    expect(missionsOf('P2')).toEqual([])
    expect(orch.getState().cars.P1.stage).toBe('to_slot')

    const done = await runUntil(ds, orch.getState, (s) => s.layout.A1 === 'P2')
    expect(done).toBe(true)
    expect(missionsOf('P2').map((cmd) => cmd.waypoints)).toEqual([['yard.Y1']])
    expect(orch.getState().layout).toMatchObject({
      A1: 'P2',
      A2: null,
      A3: 'P1',
      A4: 'P4',
      A5: 'P3',
      A6: null,
    })
    expect(feedText('P2')).toContain('YARD Waited for P1 to leave A1')
    const rejectedP3 = orch.getState().feed.filter((entry) => entry.vin === 'P3')
    expect(rejectedP3).toHaveLength(3)
    expect(rejectedP3.every((entry) => entry.tag === 'YARD' && entry.exception)).toBe(true)
  })

  it('never moves a car into a slot another car is driving to', async () => {
    const seen: string[][] = []
    const { ds, orch } = setup({
      ai: {
        replanYardSlots: async (input) => {
          seen.push(input.constraints.reservedSlots ?? [])
          return { moves: [{ vin: 'P1', from: 'A1', to: 'A3' }], reason: 'test', degraded: false }
        },
      },
    })
    orch.start([
      { vin: 'P1', colour: 'blue', parkedAt: 'A1' },
      { vin: 'P5', colour: 'blue' },
    ])
    const assigned = await runUntil(ds, orch.getState, (s) => s.cars.P5.slot !== undefined)
    expect(assigned).toBe(true)
    expect(orch.getState().cars.P5.slot).toBe('A3')
    expect(orch.getState().layout.A3).toBeNull()

    const outcome = await orch.applyYardReplan({ reason: 'test' })
    expect(seen).toEqual([['A3']])
    expect(outcome.ordered).toEqual([])
    expect(outcome.rejected.map((item) => item.reason)).toEqual(['target A3 is reserved'])
    const parked = await runUntil(ds, orch.getState, (s) => s.cars.P5.stage === 'parked')
    expect(parked).toBe(true)
    expect(orch.getState().layout).toMatchObject({ A1: 'P1', A3: 'P5' })
  })
})
