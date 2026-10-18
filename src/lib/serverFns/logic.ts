// Pure decision logic for the Marshal server functions: the deterministic offline answers
// (used with no Gemini key, on error or on timeout) and the safety rules every Gemini answer is
// re-checked against. No SDKs, no env, safe to import from the browser.

import { type CandidatePath, isParked, planCandidatePaths } from '../marshal/parking/astar'
import type {
  AssistInput,
  AssistModel,
  GateInput,
  GateModel,
  LampInput,
  LampModel,
  ParkingInput,
  ParkingModel,
  RouteInput,
  RouteModel,
  YardInput,
  YardModel,
} from '../marshal/schemas'
import type {
  AssistAnalysis,
  AssistOption,
  BoundingBox,
  GateResult,
  GateView,
  LampResult,
  ParkingDecision,
  ParkingStep,
  RouteChoice,
  YardMove,
  YardReplan,
} from '../marshal/types'

const GATE_VIEWS: readonly GateView[] = ['front', 'rear', 'left', 'right']
const NEGATIVE = new Set(['', '0', 'no', 'none', 'false', 'off', 'clear', 'ok'])
const PERSON_KEYS = ['worker', 'workers', 'person', 'people', 'walker', 'walkers']
const PERSON_RE = /\b(workers?|person|people|pedestrians?|walkers?|humans?)\b/i

/**
 * Parses a truth hint such as 'colour=red; headlights=off; workers=0'.
 * @param hint - Hint string; `;` or `,` separated `key=value` pairs.
 * @returns Lower-cased key -> lower-cased value.
 */
export function parseHint(hint?: string): Record<string, string> {
  const facts: Record<string, string> = {}
  if (!hint) return facts
  for (const part of hint.split(/[;,]/)) {
    const [rawKey, ...rest] = part.split('=')
    const key = rawKey.trim().toLowerCase()
    if (key === '' || rest.length === 0) continue
    facts[key] = rest.join('=').trim().toLowerCase()
  }
  return facts
}

/** Gate fault raised offline when no evidence could be checked (the car is held). */
export const UNVERIFIED_FAULT = 'not_verified_offline'

/** Lamp `observed` value offline when the camera could not be checked. */
export const UNVERIFIED = 'UNVERIFIED'

/**
 * Clamps a number into 0..1 (NaN becomes 0).
 * @param value - Raw confidence.
 * @returns Clamped confidence.
 */
export function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(1, Math.max(0, value))
}

// --- Gate (M3.3) --------------------------------------------------------------------------------

/**
 * Whether a seen colour matches the build sheet ('dark blue' matches 'blue').
 * @param seen - Colour reported by the camera / model.
 * @param expected - Build-sheet colour.
 * @returns True on match.
 */
export function colourMatches(seen: string, expected: string): boolean {
  const a = seen.trim().toLowerCase()
  const b = expected.trim().toLowerCase()
  return a === b || a.split(/[\s_-]+/).includes(b)
}

/**
 * Deterministic gate check from per-view hints (`colour=`, `damage=`).
 * Fails on a colour mismatch with the build sheet or any reported damage. Without any hint
 * nothing was verified, so the car fails with the {@link UNVERIFIED_FAULT} fault (fail closed)
 * and is held for a person to inspect.
 * @param input - Gate input.
 * @returns Gate result with `degraded: true`.
 */
export function offlineGate(input: GateInput): GateResult {
  const expected = input.expectedColour.trim().toLowerCase()
  const faults: string[] = []
  let colour = expected
  let hinted = false
  for (const view of GATE_VIEWS) {
    const facts = parseHint(input.hints?.[view])
    if (Object.keys(facts).length > 0) hinted = true
    if (facts.colour && colour === expected && !colourMatches(facts.colour, expected)) {
      colour = facts.colour
    }
    const damage = facts.damage
    if (damage !== undefined && !NEGATIVE.has(damage)) faults.push(`damage:${view}:${damage}`)
  }
  if (!hinted) {
    return {
      pass: false,
      colour: 'unknown',
      confidence: 0,
      faults: [UNVERIFIED_FAULT],
      degraded: true,
    }
  }
  if (!colourMatches(colour, expected)) faults.unshift('colour_mismatch')
  return {
    pass: faults.length === 0,
    colour,
    confidence: 0.9,
    faults,
    degraded: true,
  }
}

/**
 * Safety rules for a Gemini gate answer: a colour mismatch is always a fault and any fault
 * fails the car, whatever the model said about `pass`.
 * @param input - Gate input.
 * @param model - Raw Gemini answer.
 * @returns Gate result with `degraded: false`.
 */
export function enforceGate(input: GateInput, model: GateModel): GateResult {
  const colour = model.colour.trim().toLowerCase()
  const faults = model.faults.map((fault) => fault.trim()).filter((fault) => fault !== '')
  if (!colourMatches(colour, input.expectedColour) && !faults.includes('colour_mismatch')) {
    faults.unshift('colour_mismatch')
  }
  return {
    pass: model.pass && faults.length === 0,
    colour,
    confidence: clamp01(model.confidence),
    faults,
    degraded: false,
  }
}

// --- Lamp (M5.5) --------------------------------------------------------------------------------

/** Telemetry keys that report each R-12 lamp step. */
const LAMP_TELEMETRY: Record<string, string[]> = {
  headlights: ['headlights', 'low_beam'],
  brake_lights: ['brake_lamps', 'brake_signal'],
  indicators: ['indicator_left', 'indicator_right'],
  reverse: ['reverse_lamps', 'gear'],
}

function lampOn(value: unknown): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value > 0
  if (typeof value !== 'string') return false
  return ['on', 'blink', 'r', 'true', '1', 'lit'].includes(value.trim().toLowerCase())
}

/**
 * Lamp state the car's telemetry claims for a routine step.
 * @param step - R-12 step name, e.g. 'headlights'.
 * @param telemetry - `track.step` telemetry.
 * @returns 'ON' or 'OFF' ('ON' when the step reports no lamp key, as R-12 commands it on).
 */
export function expectedLamp(step: string, telemetry: Record<string, unknown>): 'ON' | 'OFF' {
  const keys = LAMP_TELEMETRY[step] ?? [step]
  const values = keys.map((key) => telemetry[key]).filter((value) => value !== undefined)
  if (values.length === 0) return 'ON'
  return values.some(lampOn) ? 'ON' : 'OFF'
}

/**
 * Normalises a free-text lamp observation.
 * @param text - e.g. 'ON', 'off', 'not lit'.
 * @returns 'ON' / 'OFF', or undefined when unrecognised.
 */
export function normaliseLamp(text: string): 'ON' | 'OFF' | undefined {
  const value = text.trim().toLowerCase()
  if (/\b(off|dark|not|unlit|no)\b/.test(value)) return 'OFF'
  if (/\b(on|lit|blink|blinking|true)\b/.test(value)) return 'ON'
  return undefined
}

/**
 * Deterministic lamp check: expected from telemetry, observed from the hint
 * (`headlights=off`). Telemetry ON + camera OFF fails. Without a readable hint the camera was
 * not checked, so `observed` is {@link UNVERIFIED} and the step fails (fail closed).
 * @param input - Lamp input.
 * @returns Lamp result with `degraded: true`.
 */
export function offlineLamp(input: LampInput): LampResult {
  const expected = expectedLamp(input.step, input.telemetry)
  const facts = parseHint(input.hint)
  const raw = facts[input.step] ?? facts.lamps ?? facts.lights
  const observed = (raw === undefined ? undefined : normaliseLamp(raw)) ?? UNVERIFIED
  return { expected, observed, pass: expected === observed, degraded: true }
}

/**
 * Safety rules for a Gemini lamp answer: `expected` always comes from telemetry and `pass` is
 * recomputed in code; an unreadable observation falls back offline.
 * @param input - Lamp input.
 * @param model - Raw Gemini answer.
 * @returns Lamp result.
 */
export function enforceLamp(input: LampInput, model: LampModel): LampResult {
  const observed = normaliseLamp(model.observed)
  if (observed === undefined) return offlineLamp(input)
  const expected = expectedLamp(input.step, input.telemetry)
  return { expected, observed, pass: expected === observed, degraded: false }
}

// --- Assist (M6.6) ------------------------------------------------------------------------------

/**
 * Whether a person is indicated: hint keys (`worker=1`) win, else the reason text.
 * @param reason - Assist reason.
 * @param hint - Optional truth hint.
 * @returns True when a person is in or near the lane.
 */
export function personIndicated(reason: string, hint?: string): boolean {
  const facts = parseHint(hint)
  const hinted = PERSON_KEYS.filter((key) => facts[key] !== undefined)
  if (hinted.length > 0) return hinted.some((key) => !NEGATIVE.has(facts[key]))
  return PERSON_RE.test(reason)
}

/**
 * Deterministic option choice: wait_for_removal only for a person when offered, else
 * pass_left_3kmh when offered, else the first offered option.
 * @param options - Offered options (non-empty).
 * @param person - Whether a person is indicated.
 * @returns One of `options`.
 */
export function pickAssistOption(options: readonly AssistOption[], person: boolean): AssistOption {
  if (person && options.includes('wait_for_removal')) return 'wait_for_removal'
  if (options.includes('pass_left_3kmh')) return 'pass_left_3kmh'
  return options[0]
}

/**
 * Deterministic assist analysis from the reason and hint. Boxes are empty offline.
 * @param input - Assist input.
 * @returns Assist analysis with `degraded: true`.
 */
export function offlineAssist(input: AssistInput): AssistAnalysis {
  const person = personIndicated(input.reason, input.hint)
  const reason = input.reason.trim().replace(/[.\s]+$/, '')
  const note = person ? ' Person in or near the lane.' : ''
  return {
    description: `Car stopped: ${reason || 'lane blocked'}.${note}`,
    boxes: [],
    recommendation: pickAssistOption(input.options, person),
    degraded: true,
  }
}

function sanitiseBox(box: BoundingBox): BoundingBox | null {
  const values = [box.x, box.y, box.w, box.h]
  if (!values.every(Number.isFinite)) return null
  const x = clamp01(box.x)
  const y = clamp01(box.y)
  const w = Math.min(clamp01(box.w), 1 - x)
  const h = Math.min(clamp01(box.h), 1 - y)
  if (w <= 0 || h <= 0) return null
  return { label: box.label, x, y, w, h }
}

/**
 * Safety rules for a Gemini assist answer: the recommendation must be an offered option, and
 * when a person is indicated it never recommends passing if waiting is offered. Boxes are
 * clamped to 0-1.
 * @param input - Assist input.
 * @param model - Raw Gemini answer.
 * @returns Assist analysis with `degraded: false`.
 */
export function enforceAssist(input: AssistInput, model: AssistModel): AssistAnalysis {
  const person = personIndicated(input.reason, input.hint)
  const proposed = input.options.find((option) => option === model.recommendation)
  let recommendation = proposed ?? pickAssistOption(input.options, person)
  const canWait = input.options.includes('wait_for_removal')
  if (person && canWait && recommendation === 'pass_left_3kmh') recommendation = 'wait_for_removal'
  const boxes = model.boxes.map(sanitiseBox).filter((box): box is BoundingBox => box !== null)
  return {
    description: model.description.trim() || offlineAssist(input).description,
    boxes,
    recommendation,
    degraded: false,
  }
}

// --- Parking (M7.5) -----------------------------------------------------------------------------

const PATH_DECISIONS: Record<CandidatePath['label'], ParkingDecision> = {
  A: 'path_a',
  B: 'path_b',
  C: 'path_c',
}

/**
 * Deterministic parking step: 'parked' when already in the slot, else the cheapest A* path,
 * else 'ask_person'.
 * @param input - Parking input.
 * @param candidates - A* candidates (planned from `input` when omitted).
 * @returns Parking step with `degraded: true`.
 */
export function offlineParking(
  input: ParkingInput,
  candidates?: readonly CandidatePath[],
): ParkingStep {
  if (isParked(input.pose, input.slot)) {
    return { path: [], decision: 'parked', confidence: 1, degraded: true }
  }
  const paths = candidates ?? planCandidatePaths(input.pose, input.obstacles, input.slot)
  const cheapest = paths[0]
  if (!cheapest) return { path: [], decision: 'ask_person', confidence: 0, degraded: true }
  return {
    path: cheapest.steps,
    decision: PATH_DECISIONS[cheapest.label],
    confidence: 0.6,
    degraded: true,
  }
}

/**
 * Safety rules for a Gemini parking answer: a path decision must name an existing A*
 * candidate (the path always comes from A*, never from the model), 'parked' is only accepted
 * when the pose is within tolerance, and 'ask_person' is always accepted. Anything else falls
 * back offline.
 * @param input - Parking input.
 * @param candidates - A* candidates shown to the model.
 * @param model - Raw Gemini answer.
 * @returns Parking step.
 */
export function enforceParking(
  input: ParkingInput,
  candidates: readonly CandidatePath[],
  model: ParkingModel,
): ParkingStep {
  const decision = model.decision.trim().toLowerCase()
  const confidence = clamp01(model.confidence)
  if (decision === 'ask_person') {
    return { path: [], decision: 'ask_person', confidence, degraded: false }
  }
  if (decision === 'parked') {
    if (isParked(input.pose, input.slot)) {
      return { path: [], decision: 'parked', confidence, degraded: false }
    }
    return offlineParking(input, candidates)
  }
  const chosen = candidates.find((candidate) => PATH_DECISIONS[candidate.label] === decision)
  if (!chosen) return offlineParking(input, candidates)
  return {
    path: chosen.steps,
    decision: PATH_DECISIONS[chosen.label],
    confidence,
    degraded: false,
  }
}

// --- Route (M9.1) -------------------------------------------------------------------------------

/**
 * Deterministic route choice: lane_c when a hazard is described and lane_c is offered, else
 * finishing_corner when offered, else the first offered option.
 * @param input - Route input.
 * @returns Route choice with `degraded: true`.
 */
export function offlineRoute(input: RouteInput): RouteChoice {
  const hazard = input.hazardDescription.trim()
  if (hazard !== '' && input.options.includes('lane_c')) {
    return {
      route: 'lane_c',
      confidence: 0.7,
      reason: `Hazard at the finishing corner: ${hazard}`,
      degraded: true,
    }
  }
  const route = input.options.includes('finishing_corner') ? 'finishing_corner' : input.options[0]
  return {
    route,
    confidence: 0.6,
    reason: hazard === '' ? 'No hazard reported' : 'Lane C not offered',
    degraded: true,
  }
}

/**
 * Safety rule for a Gemini route answer: the route must be one of the offered options.
 * @param input - Route input.
 * @param model - Raw Gemini answer.
 * @returns Route choice.
 */
export function enforceRoute(input: RouteInput, model: RouteModel): RouteChoice {
  const route = input.options.find((option) => option === model.route.trim())
  if (!route) return offlineRoute(input)
  return {
    route,
    confidence: clamp01(model.confidence),
    reason: model.reason.trim() || 'Gemini route choice',
    degraded: false,
  }
}

// --- Yard (M9.2) --------------------------------------------------------------------------------

function slotOrder(a: string, b: string): number {
  return a.localeCompare(b, 'en', { numeric: true })
}

/**
 * Deterministic greedy re-plan: first vacate reserved slots, then move each priority VIN
 * (in order) into the lowest free, non-reserved slot when that slot is ahead of its current one.
 * @param input - Yard input.
 * @returns Yard re-plan with `degraded: true`.
 */
export function offlineYard(input: YardInput): YardReplan {
  const layout: Record<string, string | null> = { ...input.layout }
  const reserved = new Set(input.constraints.reservedSlots ?? [])
  const slots = Object.keys(layout).sort(slotOrder)
  const moves: YardMove[] = []
  const firstFree = () => slots.find((slot) => layout[slot] === null && !reserved.has(slot))
  const move = (vin: string, from: string, to: string) => {
    layout[to] = vin
    layout[from] = null
    moves.push({ vin, from, to })
  }

  for (const slot of slots) {
    const vin = layout[slot]
    if (vin === null || !reserved.has(slot)) continue
    const to = firstFree()
    if (to !== undefined) move(vin, slot, to)
  }
  for (const vin of input.constraints.priorityVins ?? []) {
    const from = slots.find((slot) => layout[slot] === vin)
    if (from === undefined) continue
    const to = firstFree()
    if (to !== undefined && slotOrder(to, from) < 0) move(vin, from, to)
  }

  const why = input.constraints.reason?.trim()
  const suffix = why ? ` for ${why}` : ''
  if (moves.length === 0) return { moves, reason: 'No slot moves needed', degraded: true }
  return { moves, reason: `${moves.length} slot move(s)${suffix}`, degraded: true }
}

/**
 * Validates a sequence of yard moves against the layout: each move must take a VIN from the
 * slot it is in to an existing, currently free, non-reserved slot.
 * @param input - Yard input.
 * @param moves - Proposed moves, applied in order.
 * @returns True when every move is legal.
 */
export function validYardMoves(input: YardInput, moves: readonly YardMove[]): boolean {
  const layout: Record<string, string | null> = { ...input.layout }
  const reserved = new Set(input.constraints.reservedSlots ?? [])
  for (const { vin, from, to } of moves) {
    if (!Object.hasOwn(layout, from) || !Object.hasOwn(layout, to)) return false
    if (layout[from] !== vin || layout[to] !== null || reserved.has(to)) return false
    layout[to] = vin
    layout[from] = null
  }
  return true
}

/**
 * Safety rule for a Gemini yard answer: every move must pass `validYardMoves`, else the
 * deterministic plan is used.
 * @param input - Yard input.
 * @param model - Raw Gemini answer.
 * @returns Yard re-plan.
 */
export function enforceYard(input: YardInput, model: YardModel): YardReplan {
  if (!validYardMoves(input, model.moves)) return offlineYard(input)
  return {
    moves: model.moves.map(({ vin, from, to }) => ({ vin, from, to })),
    reason: model.reason.trim() || 'Gemini yard re-plan',
    degraded: false,
  }
}
