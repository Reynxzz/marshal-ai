// Which car the dashboard 3D view focuses on. Pure: reads an orchestrator snapshot plus the
// dashboard's selection and returns the focus target (null = plant overview).

import type { CarState, MarshalState } from '#/lib/marshal/state'

/**
 * Why a car is focused: 'case' = an open assist case waits on the supervisor, 'held' = the
 * yard crew took over, 'selected' = the user clicked the car.
 */
export type FocusReason = 'case' | 'held' | 'selected'

/** The focused car. */
export type SimFocus = {
  vin: string
  reason: FocusReason
  /** Open case for the car, when there is one. */
  caseId?: string
  /** Last named map point the car passed. */
  location: string
  /** True when the user picked the car (chip, yard cell, "Show in 3D"). */
  picked: boolean
}

/** Dashboard inputs to {@link chooseFocus}. */
export type FocusInput = {
  /** Selected VIN (chips, yard cells, "Show in 3D"). */
  selected: string | null
  /** Case picked with "Show in 3D". */
  focusedCase?: string | null
  /** Auto-focus keys the user dismissed with "Overview" (see {@link autoFocusKey}). */
  dismissed?: readonly string[]
}

/**
 * Key used to dismiss an automatic focus target.
 * @param kind - 'case' or 'held'.
 * @param id - Case id or VIN.
 * @returns Stable key.
 */
export function autoFocusKey(kind: 'case' | 'held', id: string): string {
  return `${kind}:${id}`
}

function openCases(state: MarshalState) {
  return Object.values(state.cases)
    .filter((item) => item.status === 'open' && state.cars[item.vin])
    .sort((a, b) => a.opened_at - b.opened_at)
}

function isHeld(car: CarState | undefined): car is CarState {
  if (!car || car.stage === 'shipped') return false
  // A held car that later stops for an assist case is waiting on you, not the yard crew.
  return car.stage === 'held' || (car.held !== undefined && car.stage !== 'waiting')
}

function heldCars(state: MarshalState): CarState[] {
  return state.order.map((vin) => state.cars[vin]).filter(isHeld)
}

/**
 * Every automatic focus target right now (open cases oldest first, then held cars).
 * @param state - Orchestrator snapshot.
 * @returns Dismiss keys.
 */
export function autoFocusKeys(state: MarshalState): string[] {
  return [
    ...openCases(state).map((item) => autoFocusKey('case', item.case_id)),
    ...heldCars(state).map((car) => autoFocusKey('held', car.vin)),
  ]
}

function reasonFor(state: MarshalState, vin: string): { reason: FocusReason; caseId?: string } {
  const open = openCases(state).find((item) => item.vin === vin)
  if (open) return { reason: 'case', caseId: open.case_id }
  if (isHeld(state.cars[vin])) return { reason: 'held' }
  return { reason: 'selected' }
}

/**
 * Picks the focused car: the case picked with "Show in 3D", else the selected car, else the
 * oldest open assist case, else the first held car; dismissed automatic targets are skipped.
 * @param state - Orchestrator snapshot.
 * @param input - Selection, picked case and dismissed keys.
 * @returns Focus target, or null for the plant overview.
 */
export function chooseFocus(state: MarshalState, input: FocusInput): SimFocus | null {
  const dismissed = new Set(input.dismissed ?? [])
  const make = (vin: string, picked: boolean): SimFocus | null => {
    const car = state.cars[vin]
    if (!car || car.stage === 'shipped') return null
    return { vin, location: car.location, picked, ...reasonFor(state, vin) }
  }
  const picked = input.focusedCase ? state.cases[input.focusedCase] : undefined
  if (picked?.status === 'open') {
    const focus = make(picked.vin, true)
    if (focus) return { ...focus, reason: 'case', caseId: picked.case_id }
  }
  if (input.selected) {
    const focus = make(input.selected, true)
    if (focus) return focus
  }
  for (const item of openCases(state)) {
    if (dismissed.has(autoFocusKey('case', item.case_id))) continue
    const focus = make(item.vin, false)
    if (focus) return { ...focus, reason: 'case', caseId: item.case_id }
  }
  for (const car of heldCars(state)) {
    if (dismissed.has(autoFocusKey('held', car.vin))) continue
    const focus = make(car.vin, false)
    if (focus) return focus
  }
  return null
}

const REASON_TEXT: Record<FocusReason, string> = {
  case: 'waiting on you',
  held: 'held for the yard crew',
  selected: 'selected',
}

/**
 * Caption under the 3D view.
 * @param focus - Focus target (null = overview).
 * @param state - Orchestrator snapshot (for the overview car count).
 * @returns e.g. 'Focused: P002 at finishing — waiting on you'.
 */
export function focusCaption(focus: SimFocus | null, state: MarshalState): string {
  if (focus) return `Focused: ${focus.vin} at ${focus.location} — ${REASON_TEXT[focus.reason]}`
  const live = state.order.filter((vin) => state.cars[vin]?.stage !== 'shipped').length
  return `Overview: ${live} ${live === 1 ? 'car' : 'cars'} on the plant`
}

/**
 * Accessible description of the 3D view.
 * @param focus - Focus target.
 * @param state - Orchestrator snapshot.
 * @returns aria-label for the canvas.
 */
export function focusAriaLabel(focus: SimFocus | null, state: MarshalState): string {
  if (!focus) return `3D plant view. ${focusCaption(focus, state)}.`
  return `3D plant view following car ${focus.vin} at ${focus.location}, ${REASON_TEXT[focus.reason]}.`
}

/**
 * Highlight colour for the focus ring (Main.dc.html tokens).
 * @param focus - Focus target.
 * @returns Amber for decisions (needs you / held), blue for a plain selection.
 */
export function focusColour(focus: Pick<SimFocus, 'reason'>): string {
  return focus.reason === 'selected' ? '#7FA7FF' : '#E39B2E'
}
