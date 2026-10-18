import { describe, expect, it } from 'vitest'
import {
  addCar,
  addDecision,
  assignedSlots,
  colourToRgb,
  createInitialState,
  firstFreeSimSlot,
  freeSimSlots,
  heldAtReport,
  pointToSlot,
  rgbToColourName,
  setSlot,
  slotToPoint,
  yardCells,
  yardSlotIds,
} from '../state'

const car = {
  vin: 'VIN-1',
  colour: 'blue',
  expectedColour: 'blue',
  stage: 'to_gate' as const,
  location: 'line_end',
  decisions: [],
}

describe('state', () => {
  it('builds an 8x10 yard with rows A-H', () => {
    const slots = yardSlotIds()
    expect(slots).toHaveLength(80)
    expect(slots[0]).toBe('A1')
    expect(slots.at(-1)).toBe('H10')
    expect(Object.values(createInitialState().layout).every((vin) => vin === null)).toBe(true)
  })

  it('maps row A columns 1-8 to the sim yard points', () => {
    expect(slotToPoint('A7')).toBe('yard.Y7')
    expect(slotToPoint('A9')).toBeUndefined()
    expect(slotToPoint('B1')).toBeUndefined()
    expect(pointToSlot('yard.Y3')).toBe('A3')
    expect(pointToSlot('yard.entry')).toBeUndefined()
  })

  it('round-trips colour names through RGB', () => {
    for (const name of ['blue', 'red', 'white']) {
      expect(rgbToColourName(colourToRgb(name))).toBe(name)
    }
  })

  it('derives KPIs from cars and the feed without mutating the input', () => {
    const empty = createInitialState()
    const withCar = addCar(empty, car)
    const logged = addDecision(withCar, 'VIN-1', {
      at: 1,
      tag: 'GEMINI',
      text: 'Gate PASS',
      exception: false,
    })
    expect(empty.order).toEqual([])
    expect(withCar.cars['VIN-1'].decisions).toEqual([])
    expect(logged.cars['VIN-1'].decisions).toHaveLength(1)
    expect(logged.feed[0]).toMatchObject({ vin: 'VIN-1', tag: 'GEMINI' })
    expect(logged.kpis).toMatchObject({ offLine: 1, decisions: 1, inYard: 0 })
  })

  it('assigns the first free, unreserved row-A slot and reports cell states', () => {
    let state = setSlot(addCar(createInitialState(), car), 'A1', 'VIN-0')
    state = { ...state, reserved: ['A2'] }
    expect(firstFreeSimSlot(state)).toBe('A3')
    state = addCar(state, { ...car, vin: 'VIN-2', stage: 'to_slot', slot: 'A3' })
    // A4 would sit right behind A3: a slot with both neighbours free is preferred.
    expect(firstFreeSimSlot(state)).toBe('A5')
    const cells = yardCells(state)
    expect(cells.A1).toBe('parked')
    expect(cells.A3).toBe('res')
    expect(cells.A4).toBe('empty')
  })

  it('falls back to slots next to parked cars when the row fills up', () => {
    let state = createInitialState()
    for (const slot of ['A1', 'A3', 'A5', 'A7']) state = setSlot(state, slot, `V-${slot}`)
    expect(firstFreeSimSlot(state)).toBe('A8')
    state = setSlot(state, 'A8', 'V-A8')
    expect(firstFreeSimSlot(state)).toBe('A2')
  })

  it('leaves pre-parked cars out of off-the-line and progress', () => {
    let state = addCar(createInitialState(), {
      ...car,
      vin: 'P090',
      stage: 'parked',
      slot: 'A8',
      preParked: true,
    })
    state = addCar(state, car)
    expect(state.kpis).toMatchObject({ offLine: 1, inYard: 1 })
    expect(state.progress).toBe(0)
  })

  it('counts held cars and unverified inspections as finished', () => {
    let state = addCar(createInitialState(), { ...car, stage: 'held', held: 'yard full' })
    state = addCar(state, { ...car, vin: 'VIN-2', stage: 'rework', reworkReason: 'inspection' })
    state = addCar(state, { ...car, vin: 'VIN-3', stage: 'rework', reworkReason: 'lamp' })
    expect(state.progress).toBeCloseTo(2 / 3)
  })

  it('lists slots assigned to cars that are not parked in them yet', () => {
    let state = setSlot(createInitialState(), 'A1', 'VIN-0')
    state = addCar(state, { ...car, vin: 'VIN-0', stage: 'parked', slot: 'A1' })
    state = addCar(state, { ...car, vin: 'VIN-2', stage: 'parking', slot: 'A4' })
    state = addCar(state, { ...car, vin: 'VIN-3', stage: 'to_slot', slot: 'A2' })
    expect(assignedSlots(state)).toEqual(['A2', 'A4'])
  })

  it('finds cars held at the report point and marks a held car slot as alert', () => {
    let state = addCar(createInitialState(), { ...car, stage: 'held', held: 'yard full' })
    state = addCar(state, { ...car, vin: 'VIN-2', stage: 'held', held: 'abandoned', slot: 'A3' })
    expect(heldAtReport(state)).toEqual(['VIN-1'])
    expect(yardCells(state).A3).toBe('alert')
    expect(freeSimSlots(state)).toEqual(['A1', 'A2', 'A4', 'A5', 'A6', 'A7', 'A8'])
  })

  it('uses a preferred slot only when it is a free, unreserved row-A slot', () => {
    let state = setSlot(createInitialState(), 'A8', 'VIN-0')
    expect(firstFreeSimSlot(state, 'A7')).toBe('A7')
    expect(firstFreeSimSlot(state, 'A8')).toBe('A1')
    expect(firstFreeSimSlot(state, 'B1')).toBe('A1')
    state = { ...state, reserved: ['A7'] }
    expect(firstFreeSimSlot(state, 'A7')).toBe('A1')
  })
})
