// evaluateLampEvidence (TEAM_PLAN M5.5): Gemini reads the on-board evidence frames to see whether
// the lamp under test is really lit. `expected` always comes from telemetry, so a car whose
// telemetry says ON while the camera sees OFF fails. Falls back offline (degraded: true).

import { createServerFn } from '@tanstack/react-start'
import { type LampInput, lampInputSchema, lampModelSchema } from '#/lib/marshal/schemas'
import type { LampResult } from '#/lib/marshal/types'
import { enforceLamp, offlineLamp } from './logic'

export { offlineLamp }

function lampPrompt(input: LampInput): string {
  return (
    'The images are consecutive on-board evidence frames of a car during routine step ' +
    `"${input.step}" on the plant test track. Ignore the car's telemetry; judge only what the ` +
    'camera shows. Is the lamp under test visibly lit in these frames? Reply with ONLY JSON: ' +
    '{"observed":"ON"|"OFF"}'
  )
}

async function online(data: LampInput): Promise<LampResult> {
  const { generateJson } = await import('./gemini')
  const model = await generateJson(lampPrompt(data), data.frames, lampModelSchema)
  return enforceLamp(data, model)
}

/**
 * Server function: checks a lamp step from evidence frames.
 * @param data - {@link LampInput}: vin, step name, telemetry, frames and optional offline hint.
 * @returns {@link LampResult}; `degraded: true` when answered offline.
 */
export const evaluateLampEvidence = createServerFn({ method: 'POST', strict: false })
  .validator((input: unknown): LampInput => lampInputSchema.parse(input))
  .handler(async ({ data }): Promise<LampResult> => {
    try {
      const { runGemini } = await import('./gemini')
      return await runGemini(
        'evaluateLampEvidence',
        () => offlineLamp(data),
        () => online(data),
      )
    } catch {
      return offlineLamp(data)
    }
  })
