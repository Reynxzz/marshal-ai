// analyzeAssistClip (TEAM_PLAN M6.6): Gemini describes the stopped car's chase-camera clip,
// returns bounding boxes for the dashboard overlay and recommends one of the offered options.
// The recommendation is re-checked in code. Falls back offline (degraded: true).

import { createServerFn } from '@tanstack/react-start'
import { type AssistInput, assistInputSchema, assistModelSchema } from '#/lib/marshal/schemas'
import type { AssistAnalysis } from '#/lib/marshal/types'
import { enforceAssist, offlineAssist } from './logic'

export { offlineAssist }

function assistPrompt(input: AssistInput): string {
  return (
    'The images are consecutive chase-camera frames of an autonomous plant car that stopped ' +
    `because: ${input.reason}.\n` +
    `Offered options: ${input.options.join(', ')}.\n` +
    'Describe what blocks the lane in one or two sentences, give bounding boxes (normalised ' +
    '0-1, top-left origin) for each obstacle and person, and recommend exactly one offered ' +
    'option. Never recommend passing a person. Reply with ONLY JSON: {"description":string,' +
    '"boxes":[{"label":string,"x":number,"y":number,"w":number,"h":number}],' +
    '"recommendation":string}'
  )
}

async function online(data: AssistInput): Promise<AssistAnalysis> {
  const { generateJson } = await import('./gemini')
  const model = await generateJson(assistPrompt(data), data.frames, assistModelSchema)
  return enforceAssist(data, model)
}

/**
 * Server function: analyses an assist clip.
 * @param data - {@link AssistInput}: case id, reason, offered options, frames, optional hint.
 * @returns {@link AssistAnalysis}; `degraded: true` when answered offline.
 */
export const analyzeAssistClip = createServerFn({ method: 'POST', strict: false })
  .validator((input: unknown): AssistInput => assistInputSchema.parse(input))
  .handler(async ({ data }): Promise<AssistAnalysis> => {
    try {
      const { runGemini } = await import('./gemini')
      return await runGemini(
        'analyzeAssistClip',
        () => offlineAssist(data),
        () => online(data),
      )
    } catch {
      return offlineAssist(data)
    }
  })
