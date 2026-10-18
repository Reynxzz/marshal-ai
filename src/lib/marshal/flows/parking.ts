// Parking flow (TEAM_PLAN M7): from `yard.entry`, loop up to MAX_PARKING_STEPS times: read the
// obstacles around the slot, ask Gemini (over the A* candidates) for the next step, then execute
// it. 'parked' marks the slot; 'ask_person' (or running out of steps) raises an assist case, and
// on the last attempt the car is held ('held' stage) for the yard crew instead.

import { toSlotFrame } from '../parking/astar'
import { MAX_OBSTACLES } from '../schemas'
import { patchCar, setSlot, slotToPoint } from '../state'
import type { ControlStep, ParkingDecision, ParkingSlot } from '../types'
import type { FlowContext } from './context'

/** Planning steps per parking attempt. */
export const MAX_PARKING_STEPS = 8

/** Attempts (each may end in an assist case) before the yard crew takes over. */
export const MAX_PARKING_ATTEMPTS = 2

/**
 * Where the approach drive stops: this far behind the slot centre, still in the yard lane (room
 * for the S-curve in). The planner and Gemini take over from there, as in the CARLA yard.
 */
export const APPROACH_BEHIND_M = 9

/** Wheelbase and steering lock of the plant car (TEAM_PLAN M2.2). */
const CAR_WHEELBASE_M = 2.85
const MAX_STEER_RAD = (30 * Math.PI) / 180

/** The approach drive only runs while the car is lined up with the slot (degrees). */
const APPROACH_HEADING_DEG = 15

/** Approach speed, m/s (10 km/h). */
const APPROACH_SPEED_MPS = 2.8

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
  await approachSlot(ctx, vin, slot)

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
      await centreInSlot(ctx, vin, slot)
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

/**
 * Drives the car onto the yard lane and up to {@link APPROACH_BEHIND_M} behind the slot, so the
 * parking search starts next to the slot instead of at `yard.entry` (it plans a few metres, not
 * a 100-degree turn plus 40 m). A car that arrives across the lane first backs up as needed and
 * turns onto it on one arc. Only for engines that drive in the yard lane (`laneApproach`).
 */
async function approachSlot(ctx: FlowContext, vin: string, slot: ParkingSlot): Promise<void> {
  if (!ctx.parking.laneApproach) return
  const lane = toSlotFrame({ ...ctx.ds.getTransform('yard.entry') }, slot).y
  const start = toSlotFrame(ctx.ds.getPositionXY(vin), slot)
  if (start.x > -APPROACH_BEHIND_M - 1) return
  const steps = turnOntoLane(start, lane)
  const result = steps.length > 0 ? await ctx.parking.execute(vin, steps) : { stopped: false }
  if (result.stopped) {
    ctx.decide(vin, 'PLANT', `Safety stop turning into the yard lane for ${slot.id}`, {
      exception: true,
    })
    return
  }
  const local = toSlotFrame(ctx.ds.getPositionXY(vin), slot)
  const ahead = -APPROACH_BEHIND_M - local.x
  if (Math.abs((local.th * 180) / Math.PI) > APPROACH_HEADING_DEG || ahead < 1) return
  const drive = await ctx.parking.execute(vin, [
    { gear: 'D', wheel: 0, speed_mps: APPROACH_SPEED_MPS, duration_s: ahead / APPROACH_SPEED_MPS },
  ])
  if (drive.stopped) {
    ctx.decide(vin, 'PLANT', `Safety stop driving up to ${slot.id}`, { exception: true })
  }
}

/** A parked car further than this from the slot centre, fore/aft, is nudged back to it. */
const CENTRE_TOLERANCE_M = 0.1

/**
 * Nudges a parked car straight along its heading to the slot centre (lane-driving engines only).
 * "Parked" accepts 0.5 m fore/aft, but row slots leave 0.6 m of slack in all, so a car left at
 * the edge walls in the slot next to it.
 */
async function centreInSlot(ctx: FlowContext, vin: string, slot: ParkingSlot): Promise<void> {
  if (!ctx.parking.laneApproach) return
  const local = toSlotFrame(ctx.ds.getPositionXY(vin), slot)
  if (Math.abs(local.x) <= CENTRE_TOLERANCE_M) return
  await ctx.parking.execute(vin, [
    {
      gear: local.x < 0 ? 'D' : 'R',
      wheel: 0,
      speed_mps: 0.5,
      duration_s: Math.abs(local.x) / Math.cos(local.th) / 0.5,
    },
  ])
}

/** Turning radius of the approach arc, metres (just above the 4.9 m minimum). */
const APPROACH_RADIUS_M = 5.2

/** Manoeuvring speed for the turn, m/s. */
const TURN_SPEED_MPS = 1.4

/**
 * Control steps that bring a car (slot frame, heading `th` radians) onto the lane line
 * `y = lane` heading along the slot: a straight leg forward or back along the current heading,
 * then one arc of {@link APPROACH_RADIUS_M}. Empty when the car is already lined up.
 */
function turnOntoLane(start: { x: number; y: number; th: number }, lane: number): ControlStep[] {
  if (Math.abs(start.th) < (APPROACH_HEADING_DEG * Math.PI) / 180) return []
  const right = start.th > 0 ? 1 : -1
  const sin = Math.sin(start.th)
  if (Math.abs(sin) < 0.2) return []
  // End lateral offset after the arc: y + d*sin(th) + right * R * (1 - cos(th)).
  const leg = (lane - start.y - right * APPROACH_RADIUS_M * (1 - Math.cos(start.th))) / sin
  const wheel = Math.atan(CAR_WHEELBASE_M / APPROACH_RADIUS_M) / MAX_STEER_RAD
  const steps: ControlStep[] = []
  if (Math.abs(leg) > 0.05) {
    steps.push({
      gear: leg > 0 ? 'D' : 'R',
      wheel: 0,
      speed_mps: TURN_SPEED_MPS,
      duration_s: Math.abs(leg) / TURN_SPEED_MPS,
    })
  }
  steps.push({
    gear: 'D',
    wheel: -right * wheel,
    speed_mps: TURN_SPEED_MPS,
    duration_s: (APPROACH_RADIUS_M * Math.abs(start.th)) / TURN_SPEED_MPS,
  })
  return steps
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
