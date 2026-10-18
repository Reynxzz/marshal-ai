// Lamp flow (TEAM_PLAN M5.6). R-12 step 1 (headlights) is checked on the front evidence camera
// and step 3 (brake_lights) on the rear one. Telemetry always claims ON, so a dark lamp on camera
// fails the step and sends the car to `rework.electrical`. After the repair the failed steps are
// re-run (routine_steps) and a PASS sends the car on to `report_point`. Evidence frames are
// trimmed to the server function limits first (most recent frames kept).

import { offlineLamp, UNVERIFIED } from '../../serverFns/logic'
import { fitFrames } from '../frames'
import { patchCar } from '../state'
import type { TrackStepEvent } from '../types'
import type { FlowContext, MissionRecord } from './context'

/** R-12 steps checked on camera. */
export const LAMP_STEPS: Record<number, { name: string; camera: 'front' | 'rear' }> = {
  1: { name: 'headlights', camera: 'front' },
  3: { name: 'brake_lights', camera: 'rear' },
}

/** Last step of a full R-12 run. */
export const R12_FINAL_STEP = 6

/**
 * Handles one `track.step` event.
 * @param ctx - Flow context.
 * @param event - Step event from the engine.
 * @returns Resolves once the step (and, for the final step, the routine) is handled.
 */
export async function handleTrackStep(ctx: FlowContext, event: TrackStepEvent): Promise<void> {
  const mission = ctx.mission(event.vin)
  if (!mission || mission.id !== event.mission_id || mission.purpose !== 'track') return

  if (LAMP_STEPS[event.step]) {
    const check = evaluateStep(ctx, mission, event)
    mission.pending.push(check)
  }

  const final = mission.routine_steps ? Math.max(...mission.routine_steps) : R12_FINAL_STEP
  if (event.step !== final) return
  await Promise.all(mission.pending)
  if (ctx.mission(event.vin) !== mission || mission.failed.length > 0) return

  ctx.decide(event.vin, 'PLANT', `${event.routine} complete; driving to report point`)
  ctx.update((state) => patchCar(state, event.vin, { stage: 'report' }))
  ctx.go(event.vin, ['report_point'], 'report')
}

async function evaluateStep(
  ctx: FlowContext,
  mission: MissionRecord,
  event: TrackStepEvent,
): Promise<void> {
  const lamp = LAMP_STEPS[event.step]
  const fitted = fitFrames(ctx.evidence.getEvidenceFrames(event.vin, lamp.camera))
  const hint = ctx.hints?.evidenceHint?.(event.vin, lamp.camera)
  const input = {
    vin: event.vin,
    step: event.name || lamp.name,
    telemetry: event.telemetry,
    frames: fitted.frames,
    ...(hint ? { hint } : {}),
  }
  if (fitted.oversized > 0) {
    const dropped = `dropped ${fitted.oversized} evidence frame(s) too large to send`
    const text = `R-12 step ${event.step}: ${dropped}`
    ctx.decide(event.vin, 'PLANT', text)
  }
  // Every frame was too large: answer offline, as when Gemini is unavailable.
  const result =
    fitted.frames.length > 0 || fitted.oversized === 0
      ? await ctx.ai.evaluateLampEvidence(input)
      : offlineLamp(input)
  if (ctx.mission(event.vin) !== mission) return

  const verdict = result.pass ? 'PASS' : 'FAIL'
  const seen = `telemetry ${result.expected}, camera ${result.observed}`
  const text = `R-12 step ${event.step} ${lamp.name}: ${seen} -> ${verdict}`
  ctx.decide(event.vin, 'GEMINI', text, {
    exception: !result.pass,
    degraded: result.degraded,
    invalidInput: result.invalidInput,
  })
  if (result.pass) return

  mission.failed.push(event.step)
  // An unverifiable camera check (Gemini down, no hint) is held for a person, not repaired.
  const held = ctx.state().cars[event.vin]?.reworkReason === 'inspection'
  const reworkReason = held || result.observed === UNVERIFIED ? 'inspection' : 'lamp'
  ctx.update((state) => patchCar(state, event.vin, { stage: 'rework', reworkReason }))
  const rework = ctx.go(event.vin, ['rework.electrical'], 'rework')
  rework.routine_steps = [...mission.failed]
}

/**
 * Handles arrival at `rework.electrical`: lamp failures are repaired and re-checked on the test
 * track; gate failures stay in the bay.
 * @param ctx - Flow context.
 * @param vin - Car in the rework bay.
 */
export function handleReworkDone(ctx: FlowContext, vin: string): void {
  const car = ctx.state().cars[vin]
  if (!car) return
  ctx.update((state) => patchCar(state, vin, { location: 'rework.electrical' }))
  if (car.reworkReason !== 'lamp') {
    const why =
      car.reworkReason === 'inspection'
        ? 'Held in the rework bay: AI check unavailable, a person inspects the car'
        : 'Held in the rework bay for body / paint repair'
    ctx.decide(vin, 'PLANT', why, { exception: true })
    return
  }

  const steps = ctx.mission(vin)?.routine_steps ?? [1]
  const defects = steps.map((step) => LAMP_STEPS[step]?.name).filter((name) => name !== undefined)
  ctx.ds.repair(vin, defects)
  ctx.decide(
    vin,
    'PLANT',
    `Rework repaired ${defects.join(', ')}; re-checking R-12 step ${steps.join(', ')}`,
  )
  ctx.update((state) => patchCar(state, vin, { stage: 'test_track' }))
  ctx.go(vin, ['test_track'], 'track', { routine: 'R-12', routine_steps: steps })
}
