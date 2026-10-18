// Lamp flow (TEAM_PLAN M5.6). R-12 step 1 (headlights) is checked on the front evidence camera
// and step 3 (brake_lights) on the rear one. Telemetry always claims ON, so a dark lamp on camera
// fails the step and sends the car to `rework.electrical`. After the repair the failed steps are
// re-run (routine_steps) and a PASS sends the car on to `report_point`. Evidence frames are
// trimmed to the server function limits first (most recent frames kept).

import { offlineLamp, UNVERIFIED } from '../../serverFns/logic'
import { fitFrames } from '../frames'
import { type AssistCase, colourToRgb, patchCar, patchCase, upsertCase } from '../state'
import type { CaseOption, TrackStepEvent } from '../types'
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
  // A camera check that confirmed nothing is held for a person, not repaired: either Gemini answered
  // but was not sure enough (below the decision threshold, so no verdict is trusted), or it could
  // not be reached at all and there was no evidence to answer offline from.
  const held = ctx.state().cars[event.vin]?.reworkReason === 'inspection'
  const unverified = result.observed === UNVERIFIED
  const reworkReason = held || unverified ? 'inspection' : 'lamp'
  const lampName = lamp.name.replace(/_/g, ' ')
  const inspectWhy = !unverified
    ? undefined
    : result.degraded
      ? 'the AI check was unavailable'
      : `Gemini was not sure the ${lampName} are lit`
  ctx.update((state) =>
    patchCar(state, event.vin, {
      stage: 'rework',
      reworkReason,
      ...(inspectWhy ? { inspectWhy } : {}),
    }),
  )
  // A person decides on it from the photo Gemini could not confirm (a card in Waiting on you).
  if (unverified) ctx.reviews.set(event.vin, { lamp: lampName, frame: fitted.frames.at(-1) })
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
  if (car.reworkReason === 'inspection') {
    const reason = car.inspectWhy ?? 'the AI check was unavailable'
    const why = `Held in the rework bay: ${reason}, so a person inspects the car`
    ctx.decide(vin, 'PLANT', why, { exception: true })
    openLampReview(ctx, vin, reason)
    return
  }
  if (car.reworkReason === 'gate') {
    // The paint bay: repainted in the build-sheet colour, then back through the gate for a fresh
    // check (it used to stay in the bay for the rest of the shift).
    ctx.repaints.set(vin, ctx.now() + REPAINT_S)
    const minutes = Math.round(REPAINT_S / 60)
    ctx.decide(vin, 'PLANT', `In the paint bay: repainting ${car.expectedColour} (${minutes} min)`)
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

/** Sim seconds the paint bay takes to repaint a wrong-colour car (compressed for the demo). */
export const REPAINT_S = 120

/**
 * Sends every repainted car back to the gate: called on each orchestrator tick.
 * @param ctx - Flow context.
 */
export function completeRepaints(ctx: FlowContext): void {
  if (ctx.repaints.size === 0) return
  const now = ctx.now()
  for (const [vin, readyAt] of [...ctx.repaints]) {
    if (now < readyAt) continue
    ctx.repaints.delete(vin)
    const car = ctx.state().cars[vin]
    if (car?.stage !== 'rework') continue
    ctx.ds.repaint?.(vin, colourToRgb(car.expectedColour))
    ctx.update((state) => patchCar(state, vin, { colour: car.expectedColour, stage: 'to_gate' }))
    ctx.decide(vin, 'PLANT', `Repainted ${car.expectedColour}; back to the gate for a re-check`)
    ctx.go(vin, ['inspection_gate'], 'gate')
  }
}

/**
 * Opens a "Waiting on you" card for a car held after a lamp check nothing confirmed: the photo
 * Gemini judged, and a person's two calls (lamps fine, or repair and re-check).
 * @param ctx - Flow context.
 * @param vin - Car held in the rework bay.
 * @param reason - Why it is held (Gemini unsure, or the AI check unavailable).
 */
function openLampReview(ctx: FlowContext, vin: string, reason: string): void {
  const pending = ctx.reviews.get(vin)
  if (!pending) return
  ctx.reviews.delete(vin)
  const count = Object.values(ctx.state().cases).filter((item) => item.vin === vin).length
  const item: AssistCase = {
    case_id: `RV-${vin}-${count + 1}`,
    vin,
    kind: 'review',
    reason: `Lamp check: ${reason}. Look at the ${pending.lamp} in the photo`,
    options: ['lamps_ok', 'to_electrical_bay'],
    frames: pending.frame ? [pending.frame] : [],
    status: 'open',
    opened_at: ctx.now(),
  }
  ctx.update((state) => upsertCase(state, item))
}

/**
 * A person's call on a review card: the lamps are fine (on to the report point), or they are not
 * (the electrical bay repairs them and the track re-checks the failed steps).
 * @param ctx - Flow context.
 * @param item - The open review case.
 * @param option - The person's call.
 * @param by - Who decided.
 * @returns True when the call was applied.
 */
export function chooseReview(
  ctx: FlowContext,
  item: AssistCase,
  option: CaseOption,
  by: 'supervisor' | 'gemini',
): boolean {
  if (option !== 'lamps_ok' && option !== 'to_electrical_bay') return false
  const vin = item.vin
  const car = ctx.state().cars[vin]
  if (car?.stage !== 'rework') return false
  const decided = { status: 'decided' as const, chosen: option, chosen_by: by }
  ctx.update((state) => patchCase(state, item.case_id, decided))
  const tag = by === 'supervisor' ? 'SUPERVISOR' : 'GEMINI'
  if (option === 'lamps_ok') {
    const text = `${item.case_id}: lamps checked by a person, OK; on to the report point`
    ctx.decide(vin, tag, text, { exception: true })
    ctx.update((state) =>
      patchCar(state, vin, { stage: 'report', reworkReason: undefined, inspectWhy: undefined }),
    )
    ctx.go(vin, ['report_point'], 'report')
    return true
  }
  const text = `${item.case_id}: lamps not lit; to the electrical bay for repair`
  ctx.decide(vin, tag, text, { exception: true })
  ctx.update((state) => patchCar(state, vin, { reworkReason: 'lamp', inspectWhy: undefined }))
  handleReworkDone(ctx, vin)
  return true
}
