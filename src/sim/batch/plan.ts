// Batch planning (T13 extension): the AI picks case-library ids for the batch; code validates
// the answer and falls back to a seeded sample so a batch never blocks on the model.

import type { AiTaskFn } from '#/ai/tasks'
import { makeRng } from '../rng'
import { caseMenu, sampleCaseIds, validateCaseIds } from './cases'
import { deriveSeed } from './manifest'

export type BatchPlan = {
  caseIds: string[]
  rationale: string
  source: 'gemini' | 'offline'
}

export async function planBatch(
  ai: AiTaskFn,
  input: { batchId: string; cars: number },
  degraded?: () => boolean,
): Promise<BatchPlan> {
  const seed = deriveSeed(input.batchId)
  const fallback = sampleCaseIds(makeRng(seed), input.cars)
  const fromFallback = (rationale: string): BatchPlan => ({
    caseIds: fallback,
    rationale,
    source: 'offline',
  })

  try {
    const res = await ai('batch.plan', { cars: input.cars, seed, menu: caseMenu() })
    const valid = validateCaseIds(res.caseIds, input.cars)
    if (!valid) return fromFallback('Model plan rejected by validation; used a seeded sample.')
    return {
      caseIds: valid,
      rationale: res.rationale || 'Model selected the batch from the case library.',
      source: degraded?.() ? 'offline' : 'gemini',
    }
  } catch {
    return fromFallback('Planner unavailable; used a seeded sample.')
  }
}
