// replanYardSlots (TEAM_PLAN M9.2): on a late / early truck, Gemini proposes slot-to-slot moves.
// Every move is validated against the layout in code. Falls back to the deterministic greedy
// plan (degraded: true).

import { createServerFn } from '@tanstack/react-start'
import { type YardInput, yardInputSchema, yardModelSchema } from '#/lib/marshal/schemas'
import type { YardReplan } from '#/lib/marshal/types'
import { enforceYard, offlineYard } from './logic'

export { offlineYard }

function yardPrompt(input: YardInput): string {
  return (
    'You re-plan the finished-car yard. Layout (slot -> VIN, null = free): ' +
    `${JSON.stringify(input.layout)}.\n` +
    `Constraints: ${JSON.stringify(input.constraints)}.\n` +
    'Vacate reserved slots and bring priority VINs to the lowest free slots with as few moves as ' +
    'possible. Moves run in order; a move may only target a free, non-reserved slot. Reply with ' +
    'ONLY JSON: {"moves":[{"vin":string,"from":string,"to":string}],"reason":string}'
  )
}

async function online(data: YardInput): Promise<YardReplan> {
  const { generateJson } = await import('./gemini')
  const model = await generateJson(yardPrompt(data), [], yardModelSchema)
  return enforceYard(data, model)
}

/**
 * Server function: re-plans yard slots.
 * @param data - {@link YardInput}: current layout and constraints.
 * @returns {@link YardReplan}; `degraded: true` when answered offline.
 */
export const replanYardSlots = createServerFn({ method: 'POST', strict: false })
  .validator((input: unknown): YardInput => yardInputSchema.parse(input))
  .handler(async ({ data }): Promise<YardReplan> => {
    try {
      const { runGemini } = await import('./gemini')
      return await runGemini(
        'replanYardSlots',
        () => offlineYard(data),
        () => online(data),
      )
    } catch {
      return offlineYard(data)
    }
  })
