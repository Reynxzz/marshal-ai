// Marshal orchestrator state: plain data plus pure reducers. Every reducer returns a new state
// object (inputs are never mutated) and `derive` recomputes the KPIs, progress and paused flag,
// so dashboard snapshots can be compared by reference.

import type { AssistAnalysis, AssistOption } from './types'

/**
 * Where a car is in the shift (drives the dashboard lanes). 'waiting' means an assist case is
 * open for the car; 'held' means automation gave up (full yard, parking abandoned) and the yard
 * crew takes over, with nothing for the supervisor to decide.
 */
export type CarStage =
  | 'queued'
  | 'to_gate'
  | 'at_gate'
  | 'test_track'
  | 'report'
  | 'to_slot'
  | 'parking'
  | 'parked'
  | 'rework'
  | 'waiting'
  | 'held'
  | 'shipped'

/** Who made a decision (dashboard `.tag.*` classes). */
export type DecisionTag = 'GEMINI' | 'YARD' | 'SUPERVISOR' | 'PLANT'

/** One logged decision. */
export type Decision = {
  /** Sim time in seconds. */
  at: number
  tag: DecisionTag
  text: string
  /** Shown under "Exceptions only". */
  exception: boolean
  /** True when the answer came from the offline fallback. */
  degraded?: boolean
  /** True when the AI input failed schema validation (answered offline, not sent). */
  invalidInput?: boolean
}

/** A decision in the global feed. */
export type FeedEntry = Decision & { vin: string }

/** One car on the shift. */
export type CarState = {
  vin: string
  /** Actual paint colour. */
  colour: string
  /** Build-sheet colour. */
  expectedColour: string
  stage: CarStage
  /** Last named map point passed. */
  location: string
  /** Assigned yard slot id (e.g. 'A3'). */
  slot?: string
  decisions: Decision[]
  /** Stage to return to when an assist case is decided. */
  resumeStage?: CarStage
  /**
   * Why the car went to rework. Gate failures stay in the bay, and so do lamp checks that
   * could not be verified ('inspection': a person checks the lamps).
   */
  reworkReason?: 'gate' | 'lamp' | 'inspection'
  /** Sim time the car was parked (for the "just parked" cell state). */
  parkedAt?: number
  /** Already in the yard when the shift started: not counted as off the line or in progress. */
  preParked?: boolean
  /**
   * Why the car is held with nothing left to automate (full yard, parking abandoned). A held
   * car counts as finished for shift progress; the yard crew takes over. Set together with the
   * 'held' stage; a car held at the report point (no slot) is offered a slot again when one frees.
   */
  held?: string
  /** Requested yard slot (scenario), used at the report point when it is free. */
  targetSlot?: string
}

/** A "Waiting on you" case. */
export type AssistCase = {
  case_id: string
  vin: string
  reason: string
  options: AssistOption[]
  /** base64 JPEG clip frames. */
  frames: string[]
  analysis?: AssistAnalysis
  status: 'open' | 'decided'
  chosen?: AssistOption
  chosen_by?: 'supervisor' | 'gemini'
  /** Sim time the case was opened. */
  opened_at: number
}

/** A dock truck ('waiting' = here early, 'late' = running late). */
export type Truck = {
  id: string
  state: 'truck' | 'loading' | 'waiting' | 'late' | 'left'
  /** Yard slot ids loaded onto this truck. */
  slots: string[]
}

/** The six dashboard KPI tiles. */
export type Kpis = {
  offLine: number
  decisions: number
  neededYou: number
  inYard: number
  shipped: number
  rework: number
}

/** Full orchestrator state snapshot. */
export type MarshalState = {
  cars: Record<string, CarState>
  /** VINs in spawn order. */
  order: string[]
  cases: Record<string, AssistCase>
  trucks: Truck[]
  /** Yard slot id ('A1'..'H10') -> parked VIN or null. */
  layout: Record<string, string | null>
  /** Slots that must not receive cars (yard re-plan constraint). */
  reserved: string[]
  kpis: Kpis
  /** Every decision, oldest first. */
  feed: FeedEntry[]
  /** 0-1 share of cars that finished the shift. */
  progress: number
  /** Seconds. */
  simTime: number
  /** True when the shift should pause for the supervisor. */
  paused: boolean
  pauseWhenNeeded: boolean
  /** Current hazard description at the finishing corner, if any. */
  hazard: string | null
}

/** Dashboard yard cell states (`.cell.*`). */
export type YardCellState = 'empty' | 'parked' | 'fresh' | 'res' | 'alert'

/** Yard grid rows. */
export const YARD_ROWS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'] as const

/** Yard grid columns per row. */
export const YARD_COLS = 10

/** How long a parked car shows as "just parked", in sim seconds. */
export const FRESH_SECONDS = 30

/** Named paint colours and the RGB (0-1) the simulator renders them with. */
export const COLOUR_RGB: Record<string, [number, number, number]> = {
  blue: [0.1, 0.25, 0.75],
  red: [0.75, 0.1, 0.1],
  white: [0.92, 0.92, 0.92],
  black: [0.05, 0.05, 0.05],
  silver: [0.65, 0.66, 0.68],
  green: [0.1, 0.5, 0.2],
}

/**
 * RGB for a named colour (unknown names render grey).
 * @param name - Colour name, e.g. 'blue'.
 * @returns RGB triple in 0-1.
 */
export function colourToRgb(name: string): [number, number, number] {
  return COLOUR_RGB[name.trim().toLowerCase()] ?? [0.5, 0.5, 0.5]
}

/**
 * Nearest named colour for an RGB triple.
 * @param rgb - RGB triple in 0-1.
 * @returns Colour name from {@link COLOUR_RGB}.
 */
export function rgbToColourName(rgb: readonly [number, number, number]): string {
  let best = 'blue'
  let bestDistance = Number.POSITIVE_INFINITY
  for (const [name, ref] of Object.entries(COLOUR_RGB)) {
    const distance = (rgb[0] - ref[0]) ** 2 + (rgb[1] - ref[1]) ** 2 + (rgb[2] - ref[2]) ** 2
    if (distance < bestDistance) {
      best = name
      bestDistance = distance
    }
  }
  return best
}

/**
 * Every yard slot id in grid order.
 * @returns ['A1', ..., 'A10', 'B1', ..., 'H10'].
 */
export function yardSlotIds(): string[] {
  return YARD_ROWS.flatMap((row) =>
    Array.from({ length: YARD_COLS }, (_, index) => `${row}${index + 1}`),
  )
}

/**
 * An empty 8x10 yard.
 * @returns Slot id -> null for every slot.
 */
export function createYardLayout(): Record<string, string | null> {
  return Object.fromEntries(yardSlotIds().map((slot) => [slot, null]))
}

/**
 * Simulator point for a yard slot: row A columns 1-8 map to `yard.Y1`..`yard.Y8`.
 * @param slot - Yard slot id, e.g. 'A7'.
 * @returns Named point, or undefined when the slot has no sim counterpart.
 */
export function slotToPoint(slot: string): string | undefined {
  const match = /^A([1-8])$/.exec(slot)
  return match ? `yard.Y${match[1]}` : undefined
}

/**
 * Yard slot for a simulator point (inverse of {@link slotToPoint}).
 * @param point - Named point, e.g. 'yard.Y7'.
 * @returns Slot id, or undefined for non-yard points.
 */
export function pointToSlot(point: string): string | undefined {
  const match = /^yard\.Y([1-8])$/.exec(point)
  return match ? `A${match[1]}` : undefined
}

const DONE_STAGES: ReadonlySet<CarStage> = new Set(['parked', 'held', 'shipped'])

function carDone(car: CarState): boolean {
  if (DONE_STAGES.has(car.stage) || car.held !== undefined) return true
  return car.stage === 'rework' && car.reworkReason !== 'lamp'
}

/**
 * Recomputes the derived fields (KPIs, progress, paused).
 * @param state - State with up-to-date cars, cases and feed.
 * @returns State with consistent derived fields.
 */
export function derive(state: MarshalState): MarshalState {
  const cars = state.order.map((vin) => state.cars[vin]).filter(Boolean)
  const produced = cars.filter((car) => !car.preParked)
  const cases = Object.values(state.cases)
  const kpis: Kpis = {
    offLine: produced.length,
    decisions: state.feed.length,
    neededYou: cases.length,
    inYard: cars.filter((car) => car.stage === 'parked').length,
    shipped: cars.filter((car) => car.stage === 'shipped').length,
    rework: cars.filter((car) => car.reworkReason !== undefined).length,
  }
  const progress = produced.length === 0 ? 0 : produced.filter(carDone).length / produced.length
  const open = cases.some((item) => item.status === 'open')
  return { ...state, kpis, progress, paused: state.pauseWhenNeeded && open }
}

/**
 * The state before a shift starts.
 * @param pauseWhenNeeded - Initial "Pause when needed" toggle.
 * @returns Empty state.
 */
export function createInitialState(pauseWhenNeeded = true): MarshalState {
  return derive({
    cars: {},
    order: [],
    cases: {},
    trucks: [],
    layout: createYardLayout(),
    reserved: [],
    kpis: { offLine: 0, decisions: 0, neededYou: 0, inYard: 0, shipped: 0, rework: 0 },
    feed: [],
    progress: 0,
    simTime: 0,
    paused: false,
    pauseWhenNeeded,
    hazard: null,
  })
}

/**
 * Adds a car (replacing any car with the same VIN).
 * @param state - Current state.
 * @param car - New car.
 * @returns Next state.
 */
export function addCar(state: MarshalState, car: CarState): MarshalState {
  const order = state.order.includes(car.vin) ? state.order : [...state.order, car.vin]
  return derive({ ...state, cars: { ...state.cars, [car.vin]: car }, order })
}

/**
 * Patches one car; unknown VINs are ignored.
 * @param state - Current state.
 * @param vin - Car to patch.
 * @param patch - Fields to overwrite.
 * @returns Next state.
 */
export function patchCar(
  state: MarshalState,
  vin: string,
  patch: Partial<Omit<CarState, 'vin' | 'decisions'>>,
): MarshalState {
  const car = state.cars[vin]
  if (!car) return state
  return derive({ ...state, cars: { ...state.cars, [vin]: { ...car, ...patch } } })
}

/**
 * Logs a decision on the car timeline and the global feed.
 * @param state - Current state.
 * @param vin - Car the decision is about (feed-only when the car is unknown).
 * @param decision - The decision.
 * @returns Next state.
 */
export function addDecision(state: MarshalState, vin: string, decision: Decision): MarshalState {
  const car = state.cars[vin]
  const cars = car
    ? { ...state.cars, [vin]: { ...car, decisions: [...car.decisions, decision] } }
    : state.cars
  return derive({ ...state, cars, feed: [...state.feed, { ...decision, vin }] })
}

/**
 * Opens or replaces an assist case.
 * @param state - Current state.
 * @param item - The case.
 * @returns Next state.
 */
export function upsertCase(state: MarshalState, item: AssistCase): MarshalState {
  return derive({ ...state, cases: { ...state.cases, [item.case_id]: item } })
}

/**
 * Patches an assist case; unknown ids are ignored.
 * @param state - Current state.
 * @param case_id - Case to patch.
 * @param patch - Fields to overwrite.
 * @returns Next state.
 */
export function patchCase(
  state: MarshalState,
  case_id: string,
  patch: Partial<Omit<AssistCase, 'case_id'>>,
): MarshalState {
  const item = state.cases[case_id]
  if (!item) return state
  return derive({ ...state, cases: { ...state.cases, [case_id]: { ...item, ...patch } } })
}

/**
 * Sets a yard slot occupant.
 * @param state - Current state.
 * @param slot - Slot id (must exist in the grid).
 * @param vin - Occupant, or null to free the slot.
 * @returns Next state.
 */
export function setSlot(state: MarshalState, slot: string, vin: string | null): MarshalState {
  if (!Object.hasOwn(state.layout, slot)) return state
  return derive({ ...state, layout: { ...state.layout, [slot]: vin } })
}

/**
 * Slots assigned to a car that is not (yet) parked in them: cars driving or parking into a
 * slot, and yard moves in progress. They must not be offered to anyone else.
 * @param state - Current state.
 * @returns Slot ids, sorted.
 */
export function assignedSlots(state: MarshalState): string[] {
  const slots = Object.values(state.cars).flatMap((car) =>
    car.slot && state.layout[car.slot] !== car.vin ? [car.slot] : [],
  )
  return [...new Set(slots)].sort()
}

/**
 * Free row-A slots (the ones the simulator can drive to) that are neither reserved nor assigned.
 * @param state - Current state.
 * @returns Slot ids, A1 first.
 */
export function freeSimSlots(state: MarshalState): string[] {
  const taken = new Set(Object.values(state.cars).flatMap((car) => (car.slot ? [car.slot] : [])))
  const reserved = new Set(state.reserved)
  return Array.from({ length: 8 }, (_, index) => `A${index + 1}`).filter(
    (slot) => state.layout[slot] === null && !taken.has(slot) && !reserved.has(slot),
  )
}

/**
 * Free row-A slot (the ones the simulator can drive to) that is neither reserved nor assigned
 * to another car. Row-A slots sit nose to tail with almost no fore/aft slack, so a slot whose
 * neighbours are both empty is preferred, then one with a single occupied neighbour, then any.
 * @param state - Current state.
 * @param preferred - Slot to use when it is free (e.g. a scenario's requested slot).
 * @returns Slot id, or undefined when the sim yard is full.
 */
export function firstFreeSimSlot(state: MarshalState, preferred?: string): string | undefined {
  const free = freeSimSlots(state)
  if (preferred && free.includes(preferred)) return preferred
  const taken = new Set(Object.values(state.cars).flatMap((car) => (car.slot ? [car.slot] : [])))
  const occupied = (col: number) => {
    const slot = `A${col}`
    return (state.layout[slot] ?? null) !== null || taken.has(slot)
  }
  let best: { slot: string; neighbours: number } | undefined
  for (const slot of free) {
    const col = Number(slot.slice(1))
    const neighbours = Number(occupied(col - 1)) + Number(occupied(col + 1))
    if (!best || neighbours < best.neighbours) best = { slot, neighbours }
    if (neighbours === 0) break
  }
  return best?.slot
}

/**
 * Cars held at the report point because the yard was full (held, no slot assigned).
 * @param state - Current state.
 * @returns VINs in spawn order.
 */
export function heldAtReport(state: MarshalState): string[] {
  return state.order.filter((vin) => {
    const car = state.cars[vin]
    return car?.stage === 'held' && car.slot === undefined
  })
}

/**
 * Dashboard cell state per yard slot.
 * @param state - Current state.
 * @returns Slot id -> cell state ('alert' for a car waiting on you or held for the yard crew,
 * 'res' for en route,
 * 'fresh' for parked within {@link FRESH_SECONDS}, 'parked', else 'empty').
 */
export function yardCells(state: MarshalState): Record<string, YardCellState> {
  const cells: Record<string, YardCellState> = {}
  for (const [slot, vin] of Object.entries(state.layout)) {
    const car = vin ? state.cars[vin] : undefined
    if (!vin) cells[slot] = 'empty'
    else if (car?.parkedAt !== undefined && state.simTime - car.parkedAt <= FRESH_SECONDS) {
      cells[slot] = 'fresh'
    } else cells[slot] = 'parked'
  }
  for (const car of Object.values(state.cars)) {
    if (!car.slot || !Object.hasOwn(cells, car.slot)) continue
    if (car.stage === 'waiting' || car.stage === 'held') cells[car.slot] = 'alert'
    else if (car.stage === 'to_slot' || car.stage === 'parking') cells[car.slot] = 'res'
  }
  return cells
}
