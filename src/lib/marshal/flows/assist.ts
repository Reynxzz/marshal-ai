// Assist flow (TEAM_PLAN M6 / M8.8): an `assist.request` opens a "Waiting on you" case, Gemini
// analyses the clip and recommends one of the offered options. With "Pause when needed" off the
// recommendation is applied automatically (chosen_by 'gemini'); otherwise the case waits for the
// supervisor. Only offered options are ever sent. The clip sent to Gemini is trimmed to the
// server function limits (most recent frames kept); the case keeps the whole clip.

import { offlineAssist } from '../../serverFns/logic'
import { fitFrames } from '../frames'
import { patchCar, patchCase, upsertCase } from '../state'
import type { AssistOption, AssistRequestEvent } from '../types'
import type { FlowContext } from './context'
import { runParking } from './parking'

/**
 * Opens a case for an `assist.request` and attaches Gemini's analysis.
 * @param ctx - Flow context.
 * @param event - Assist request from the engine.
 * @returns Resolves once the analysis is attached (and auto-applied when allowed).
 */
export async function handleAssistRequest(
  ctx: FlowContext,
  event: AssistRequestEvent,
): Promise<void> {
  if (ctx.state().cases[event.case_id]) return
  const car = ctx.state().cars[event.vin]
  const resumeStage = car?.stage === 'waiting' ? car.resumeStage : car?.stage
  ctx.update((state) =>
    upsertCase(state, {
      case_id: event.case_id,
      vin: event.vin,
      reason: event.reason,
      options: [...event.options],
      frames: [...event.clip_frames],
      status: 'open',
      opened_at: ctx.now(),
    }),
  )
  ctx.update((state) => patchCar(state, event.vin, { stage: 'waiting', resumeStage }))
  const where = event.location ? ` at ${event.location}` : ''
  ctx.decide(event.vin, 'PLANT', `Stopped${where}: ${event.reason}`, { exception: true })

  const hint = ctx.hints?.assistHint?.(event.case_id)
  const fitted = fitFrames(event.clip_frames)
  const input = {
    case_id: event.case_id,
    reason: event.reason,
    options: [...event.options],
    frames: fitted.frames,
    ...(hint ? { hint } : {}),
  }
  if (fitted.oversized > 0) {
    const text = `${event.case_id}: dropped ${fitted.oversized} clip frame(s) too large to send`
    ctx.decide(event.vin, 'PLANT', text)
  }
  // Every frame was too large: answer offline, as when Gemini is unavailable.
  const analysis =
    fitted.frames.length === 0 && fitted.oversized > 0
      ? offlineAssist(input)
      : await ctx.ai.analyzeAssistClip(input)
  ctx.update((state) => patchCase(state, event.case_id, { analysis }))
  ctx.decide(
    event.vin,
    'GEMINI',
    `${event.case_id}: ${analysis.description} Recommends ${analysis.recommendation}`,
    { exception: true, degraded: analysis.degraded, invalidInput: analysis.invalidInput },
  )

  const state = ctx.state()
  if (!state.pauseWhenNeeded && state.cases[event.case_id]?.status === 'open') {
    chooseAssist(ctx, event.case_id, analysis.recommendation, 'gemini')
  }
}

/**
 * Applies an option to an open case and sends `assist.choose`.
 * @param ctx - Flow context.
 * @param case_id - Case to decide.
 * @param option - Chosen option; must be one of the offered options.
 * @param by - Who chose.
 * @returns True when the choice was accepted and sent.
 */
export function chooseAssist(
  ctx: FlowContext,
  case_id: string,
  option: AssistOption,
  by: 'supervisor' | 'gemini',
): boolean {
  const item = ctx.state().cases[case_id]
  if (item?.status !== 'open' || !item.options.includes(option)) return false

  ctx.send({ op: 'assist.choose', case_id, vin: item.vin, option, chosen_by: by })
  const car = ctx.state().cars[item.vin]
  ctx.update((state) => {
    const decided = patchCase(state, case_id, { status: 'decided', chosen: option, chosen_by: by })
    if (car?.stage !== 'waiting') return decided
    return patchCar(decided, item.vin, {
      stage: car.resumeStage ?? 'to_gate',
      resumeStage: undefined,
    })
  })
  const tag = by === 'supervisor' ? 'SUPERVISOR' : 'GEMINI'
  ctx.decide(item.vin, tag, `${case_id}: chose ${option}`, { exception: true })

  if (ctx.parkingCases.has(case_id)) ctx.run(item.vin, runParking(ctx, item.vin))
  return true
}

/**
 * Applies Gemini's recommendation to every open case that already has an analysis (used when
 * "Pause when needed" is switched off).
 * @param ctx - Flow context.
 * @returns Number of cases decided.
 */
export function autoChooseOpen(ctx: FlowContext): number {
  let decided = 0
  for (const item of Object.values(ctx.state().cases)) {
    if (item.status !== 'open' || !item.analysis) continue
    if (chooseAssist(ctx, item.case_id, item.analysis.recommendation, 'gemini')) decided++
  }
  return decided
}
