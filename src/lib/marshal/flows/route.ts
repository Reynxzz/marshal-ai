// Route flow (TEAM_PLAN M9.1): at `report_point` Gemini picks the finishing corner or lane C
// given the current hazard description, the yard assigns a slot, and the car drives to
// `yard.entry` where the parking flow takes over. A car held here because the yard was full is
// offered a slot again as soon as one frees (see {@link retryHeldAtReport}).

import { firstFreeSimSlot, freeSimSlots, heldAtReport, patchCar, slotToPoint } from '../state'
import type { RouteOption } from '../types'
import type { FlowContext } from './context'

/** Route options offered at `report_point`. */
export const ROUTE_OPTIONS: RouteOption[] = ['finishing_corner', 'lane_c']

/** Named point each route drives through. */
export const ROUTE_POINT: Record<RouteOption, string> = {
  finishing_corner: 'finishing',
  lane_c: 'lane_c',
}

/** Decision text when a car is held at the report point for a full yard. */
export const YARD_FULL_TEXT = 'No free yard slot; holding at the report point for the yard crew'

/**
 * Handles arrival at `report_point`.
 * @param ctx - Flow context.
 * @param vin - Car at the report point.
 * @returns Resolves once the yard mission is sent (or the car is held for a full yard).
 */
export async function handleReportArrival(ctx: FlowContext, vin: string): Promise<void> {
  if (!ctx.state().cars[vin]) return
  ctx.update((state) => patchCar(state, vin, { stage: 'report', location: 'report_point' }))

  const choice = await ctx.ai.chooseRouteHazard({
    hazardDescription: ctx.state().hazard ?? '',
    options: ROUTE_OPTIONS,
  })
  const via = ROUTE_OPTIONS.includes(choice.route) ? choice.route : 'finishing_corner'
  const text = `Route via ${via} (${choice.confidence.toFixed(2)}): ${choice.reason}`
  ctx.decide(vin, 'GEMINI', text, {
    exception: via === 'lane_c',
    degraded: choice.degraded,
    invalidInput: choice.invalidInput,
  })

  const slot = firstFreeSimSlot(ctx.state(), ctx.state().cars[vin]?.targetSlot)
  if (!slot) {
    ctx.update((state) => patchCar(state, vin, { stage: 'held', held: YARD_FULL_TEXT }))
    ctx.decide(vin, 'YARD', YARD_FULL_TEXT, { exception: true })
    return
  }
  ctx.decide(vin, 'YARD', `Assigned slot ${slot} (${slotToPoint(slot)})`)
  ctx.update((state) => patchCar(state, vin, { stage: 'to_slot', slot }))
  ctx.go(vin, [ROUTE_POINT[via], 'yard.entry'], 'yard')
}

/**
 * Re-runs slot assignment for cars held at the report point, one car per free row-A slot, in
 * spawn order. The orchestrator calls this after every state change, so a held car is offered a
 * slot as soon as one frees (yard move, truck loaded, car shipped or despawned).
 * @param ctx - Flow context.
 * @returns VINs sent back through the report-point flow.
 */
export function retryHeldAtReport(ctx: FlowContext): string[] {
  const state = ctx.state()
  const held = heldAtReport(state)
  if (held.length === 0) return []
  // Cars already at the report point without a slot (arriving or retrying) claim slots first.
  const claiming = Object.values(state.cars).filter(
    (car) => car.stage === 'report' && car.location === 'report_point' && !car.slot,
  ).length
  const retried = held.slice(0, Math.max(0, freeSimSlots(state).length - claiming))
  for (const vin of retried) {
    ctx.update((current) => patchCar(current, vin, { stage: 'report', held: undefined }))
    ctx.decide(vin, 'YARD', 'A yard slot freed; retrying slot assignment')
    ctx.run(vin, handleReportArrival(ctx, vin))
  }
  return retried
}
