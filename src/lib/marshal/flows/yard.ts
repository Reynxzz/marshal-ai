// Yard re-plan flow (TEAM_PLAN M9.2 / M9.3): Gemini proposes slot moves, `validateReplan`
// rejects anything unsafe, `orderMoves` makes a car wait when its target is still occupied by a
// car that moves later (P2 waits for P1), and moves are dispatched one at a time.

import { assignedSlots, patchCar, setSlot, slotToPoint } from '../state'
import type { YardMove } from '../types'
import type { FlowContext, OrderedMove } from './context'

/** A move that failed validation. */
export type RejectedMove = { move: YardMove; reason: string }

/** Result of {@link validateReplan}. */
export type ReplanValidation = { accepted: YardMove[]; rejected: RejectedMove[] }

/** Result of {@link orderMoves}. */
export type OrderedReplan = { ordered: OrderedMove[]; rejected: RejectedMove[] }

/** Re-plan constraints (TEAM_PLAN M9.2). */
export type YardConstraints = {
  priorityVins?: string[]
  reservedSlots?: string[]
  reason?: string
}

/** Outcome of {@link requestYardReplan}. */
export type YardReplanOutcome = {
  reason: string
  degraded: boolean
  ordered: OrderedMove[]
  rejected: RejectedMove[]
}

/**
 * Validates proposed yard moves. A move is accepted when its VIN sits in `from`, `to` exists in
 * the grid, is not reserved, is not `from`, no other move uses the same VIN or target, and `to`
 * is free or vacated by another accepted move.
 * @param layout - Slot id -> VIN or null.
 * @param moves - Proposed moves.
 * @param reserved - Slots that must not receive cars.
 * @returns Accepted moves (input order) and rejected moves with reasons.
 */
export function validateReplan(
  layout: Readonly<Record<string, string | null>>,
  moves: readonly YardMove[],
  reserved: readonly string[] = [],
): ReplanValidation {
  const rejected: RejectedMove[] = []
  const reservedSet = new Set(reserved)
  const vins = new Set<string>()
  const targets = new Set<string>()
  let candidates: YardMove[] = []

  for (const move of moves) {
    const reason = structuralProblem(layout, move, reservedSet, vins, targets)
    if (reason) {
      rejected.push({ move, reason })
      continue
    }
    vins.add(move.vin)
    targets.add(move.to)
    candidates.push(move)
  }

  // A target occupied by a car that is not itself moving away can never be entered. Dropping
  // such a move can strand another one, so repeat until stable.
  let changed = true
  while (changed) {
    changed = false
    const vacated = new Set(candidates.map((move) => move.from))
    const kept: YardMove[] = []
    for (const move of candidates) {
      if (layout[move.to] === null || vacated.has(move.to)) kept.push(move)
      else {
        rejected.push({ move, reason: `target ${move.to} is occupied by ${layout[move.to]}` })
        changed = true
      }
    }
    candidates = kept
  }
  return { accepted: candidates, rejected }
}

function structuralProblem(
  layout: Readonly<Record<string, string | null>>,
  move: YardMove,
  reserved: ReadonlySet<string>,
  vins: ReadonlySet<string>,
  targets: ReadonlySet<string>,
): string | undefined {
  if (!Object.hasOwn(layout, move.to)) return `target ${move.to} does not exist`
  if (!Object.hasOwn(layout, move.from) || layout[move.from] !== move.vin) {
    return `${move.vin} is not in ${move.from}`
  }
  if (move.from === move.to) return 'move does not change slot'
  if (reserved.has(move.to)) return `target ${move.to} is reserved`
  if (vins.has(move.vin)) return `${move.vin} is moved twice`
  if (targets.has(move.to)) return `target ${move.to} is used twice`
  return undefined
}

/**
 * Orders validated moves so every move enters a free slot; a move whose target is still held by
 * a car that moves later is put after it and marked `waitsFor` that car. Cycles (swaps with no
 * free slot) are rejected.
 * @param layout - Slot id -> VIN or null.
 * @param moves - Moves accepted by {@link validateReplan}.
 * @returns Dispatch order and rejected moves.
 */
export function orderMoves(
  layout: Readonly<Record<string, string | null>>,
  moves: readonly YardMove[],
): OrderedReplan {
  const sim: Record<string, string | null> = { ...layout }
  const remaining = [...moves]
  const ordered: OrderedMove[] = []
  while (remaining.length > 0) {
    const index = remaining.findIndex((move) => sim[move.to] === null)
    if (index < 0) break
    const [move] = remaining.splice(index, 1)
    const holder = layout[move.to]
    ordered.push(holder ? { ...move, waitsFor: holder } : { ...move })
    sim[move.from] = null
    sim[move.to] = move.vin
  }
  const rejected = remaining.map((move) => ({
    move,
    reason: 'circular move needs a free slot',
  }))
  return { ordered, rejected }
}

/**
 * Asks Gemini for a yard re-plan, validates and orders it, then dispatches the moves.
 * @param ctx - Flow context.
 * @param constraints - Priority VINs, reserved slots and reason.
 * @returns The accepted dispatch order and rejected moves.
 */
export async function requestYardReplan(
  ctx: FlowContext,
  constraints: YardConstraints,
): Promise<YardReplanOutcome> {
  const reserved = constraints.reservedSlots ?? []
  ctx.update((state) => ({ ...state, reserved: [...new Set([...state.reserved, ...reserved])] }))
  const before = ctx.state()
  // Slots a car is driving or parking into are off limits too, for Gemini and the validation.
  const modelConstraints: YardConstraints = {
    ...constraints,
    reservedSlots: [...new Set([...before.reserved, ...assignedSlots(before)])],
  }
  const replan = await ctx.ai.replanYardSlots({
    layout: { ...before.layout },
    constraints: modelConstraints,
  })

  const layout = ctx.state().layout
  const blocked = [...ctx.state().reserved, ...assignedSlots(ctx.state())]
  const valid = validateReplan(layout, replan.moves, blocked)
  const order = orderMoves(layout, valid.accepted)
  const rejected = [...valid.rejected, ...order.rejected]
  for (const { move, reason } of rejected) {
    ctx.decide(move.vin, 'YARD', `Rejected move ${move.from} -> ${move.to}: ${reason}`, {
      exception: true,
      degraded: replan.degraded,
      invalidInput: replan.invalidInput,
    })
  }
  for (const move of order.ordered) {
    ctx.decide(move.vin, 'YARD', `Re-plan ${move.from} -> ${move.to}: ${replan.reason}`, {
      degraded: replan.degraded,
      invalidInput: replan.invalidInput,
    })
  }

  ctx.yard.queue.push(...order.ordered)
  if (!ctx.yard.active) dispatchNext(ctx)
  return { reason: replan.reason, degraded: replan.degraded, ordered: order.ordered, rejected }
}

/**
 * Dispatches the next queued yard move (no-op while one is running).
 * @param ctx - Flow context.
 */
export function dispatchNext(ctx: FlowContext): void {
  if (ctx.yard.active) return
  const move = ctx.yard.queue.shift()
  if (!move) return
  const layout = ctx.state().layout
  const assignedElsewhere = Object.values(ctx.state().cars).some(
    (car) => car.vin !== move.vin && car.slot === move.to && layout[move.to] !== car.vin,
  )
  if (layout[move.from] !== move.vin || layout[move.to] !== null || assignedElsewhere) {
    ctx.decide(move.vin, 'YARD', `Dropped move ${move.from} -> ${move.to}: yard changed`, {
      exception: true,
    })
    dispatchNext(ctx)
    return
  }
  if (move.waitsFor) {
    ctx.decide(move.vin, 'YARD', `Waited for ${move.waitsFor} to leave ${move.to}`)
  }

  const point = slotToPoint(move.to)
  if (!point || !ctx.state().cars[move.vin]) {
    // The sim only has row A: a car the crew moves elsewhere leaves the sim, freeing its slot.
    if (!point && ctx.state().cars[move.vin]) ctx.ds.despawn(move.vin)
    applyMove(ctx, move, 'moved by the yard crew')
    dispatchNext(ctx)
    return
  }
  ctx.yard.active = move
  ctx.update((state) => patchCar(state, move.vin, { stage: 'to_slot', slot: move.to }))
  ctx.go(move.vin, [point], 'yard_move')
}

/**
 * Completes the active yard move when its car reports `mission.done`.
 * @param ctx - Flow context.
 * @param vin - Car that finished its slot-to-slot mission.
 */
export function handleMoveDone(ctx: FlowContext, vin: string): void {
  const move = ctx.yard.active
  if (!move || move.vin !== vin) return
  ctx.yard.active = null
  applyMove(ctx, move, 'done')
  dispatchNext(ctx)
}

function applyMove(ctx: FlowContext, move: OrderedMove, how: string): void {
  const location = slotToPoint(move.to)
  const parkedAt = ctx.now()
  ctx.update((state) => setSlot(setSlot(state, move.from, null), move.to, move.vin))
  ctx.update((state) =>
    patchCar(state, move.vin, {
      stage: 'parked',
      slot: move.to,
      parkedAt,
      ...(location ? { location } : {}),
    }),
  )
  ctx.decide(move.vin, 'YARD', `Moved ${move.from} -> ${move.to} (${how})`)
  ctx.note(`YARD · ${move.vin} ${move.from} -> ${move.to}`, 'yard')
}
