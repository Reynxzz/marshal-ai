// Parking flow (TEAM_PLAN M7): from `yard.entry`, loop up to MAX_PARKING_STEPS times: read the
// obstacles around the slot, ask Gemini (over the A* candidates) for the next step, then execute
// it. 'parked' marks the slot; 'ask_person' (or running out of steps) raises an assist case, and
// on the last attempt the car is held ('held' stage) for the yard crew instead.

import { MAX_OBSTACLES } from '../schemas'
import { patchCar, setSlot, slotToPoint } from '../state'
import type { ParkingDecision, ParkingSlot } from '../types'
import type { FlowContext } from './context'

/** Planning steps per parking attempt. */
export const MAX_PARKING_STEPS = 8

/** Attempts (each may end in an assist case) before the yard crew takes over. */
export const MAX_PARKING_ATTEMPTS = 2

const DECISION_TEXT: Record<ParkingDecision, string> = {
  path_a: 'path A',
  path_b: 'path B',
  path_c: 'path C',
  parked: 'parked',
  ask_person: 'ask a person',
}

/**
 * Parks a car in its assigned yard slot.
 * @param ctx - Flow context.
 * @param vin - Car at `yard.entry` with an assigned slot.
 * @returns Resolves when the car is parked or an assist case is raised.
 */
export async function runParking(ctx: FlowContext, vin: string): Promise<void> {
  const car = ctx.state().cars[vin]
  if (!car?.slot) return
  const slotId = car.slot
  const point = slotToPoint(slotId)
  if (!point) {
    ctx.decide(vin, 'YARD', `Slot ${slotId} is not drivable in the sim`, { exception: true })
    return
  }
  const attempt = (ctx.parkingAttempts.get(vin) ?? 0) + 1
  ctx.parkingAttempts.set(vin, attempt)
  const slot: ParkingSlot = { id: point, ...ctx.ds.getTransform(point) }
  const label = point.replace('yard.', '')
  ctx.update((state) => patchCar(state, vin, { stage: 'parking' }))

  for (let i = 0; i < MAX_PARKING_STEPS; i++) {
    const pose = ctx.ds.getPositionXY(vin)
    const obstacles = nearest(ctx.parking.getObstacles(vin, slot), slot, MAX_OBSTACLES)
    const step = await ctx.ai.planParkingStep({ vin, pose, obstacles, slot })
    const confidence = step.confidence.toFixed(2)
    const text = `${vin} parking ${label}: ${DECISION_TEXT[step.decision]} (${confidence})`
    ctx.note(`GEMINI · ${text}`, 'gemini')
    ctx.decide(vin, 'GEMINI', text, {
      exception: step.decision === 'ask_person',
      degraded: step.degraded,
      invalidInput: step.invalidInput,
    })

    if (step.decision === 'parked') {
      const parkedAt = ctx.now()
      ctx.update((state) => setSlot(state, slotId, vin))
      ctx.update((state) => patchCar(state, vin, { stage: 'parked', location: point, parkedAt }))
      ctx.decide(vin, 'PLANT', `Parked in ${slotId} (${label})`)
      ctx.note(`PLANT · ${vin} parked in ${label}`, 'plant')
      return
    }
    if (step.decision === 'ask_person') {
      askPerson(ctx, vin, label, attempt, 'no safe path into the slot')
      return
    }
    const result = await ctx.parking.execute(vin, step.path)
    if (result.stopped) {
      ctx.decide(vin, 'PLANT', `Safety stop while parking ${label}`, { exception: true })
    }
  }
  askPerson(ctx, vin, label, attempt, `not parked after ${MAX_PARKING_STEPS} steps`)
}

function nearest<T extends { x: number; y: number }>(
  items: readonly T[],
  to: { x: number; y: number },
  limit: number,
): T[] {
  if (items.length <= limit) return [...items]
  const distance = (item: T) => (item.x - to.x) ** 2 + (item.y - to.y) ** 2
  return [...items].sort((a, b) => distance(a) - distance(b)).slice(0, limit)
}

function askPerson(
  ctx: FlowContext,
  vin: string,
  label: string,
  attempt: number,
  why: string,
): void {
  if (attempt >= MAX_PARKING_ATTEMPTS) {
    const abandoned = `Parking ${label} abandoned after ${attempt} attempts (${why})`
    const held = `${abandoned}; the yard crew takes over`
    ctx.update((state) => patchCar(state, vin, { stage: 'held', held }))
    ctx.decide(vin, 'YARD', held, { exception: true })
    return
  }
  const case_id = `AC-${vin}-P${attempt}`
  ctx.parkingCases.add(case_id)
  ctx.ds.raiseAssist(vin, case_id, `Parking ${label}: ${why}`, ['wait_for_removal'])
}
