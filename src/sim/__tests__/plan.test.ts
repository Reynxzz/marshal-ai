import { describe, expect, it } from 'vitest'
import { planBatch } from '../batch/plan'
import { stubAi } from './helpers'

describe('planBatch', () => {
  it('uses a valid model plan', async () => {
    const ai = stubAi(async () => ({ caseIds: ['wc-blue-red', 'clean-blue'], rationale: 'a mix' }))
    const plan = await planBatch(ai, { batchId: 'BAT-01', cars: 2 })
    expect(plan.source).toBe('gemini')
    expect(plan.caseIds).toEqual(['wc-blue-red', 'clean-blue'])
    expect(plan.rationale).toBe('a mix')
  })

  it('falls back to the same seeded sample for invalid or failed plans', async () => {
    const invalid = await planBatch(
      stubAi(async () => ({ caseIds: ['nope'], rationale: '' })),
      { batchId: 'BAT-01', cars: 2 },
    )
    const failed = await planBatch(
      stubAi(async () => {
        throw new Error('planner down')
      }),
      { batchId: 'BAT-01', cars: 2 },
    )
    expect(invalid.source).toBe('offline')
    expect(failed.source).toBe('offline')
    expect(invalid.caseIds).toHaveLength(2)
    expect(failed.caseIds).toEqual(invalid.caseIds)
  })

  it('reports offline source and keeps the ids when degraded', async () => {
    const ai = stubAi(async () => ({ caseIds: ['wc-blue-red'], rationale: 'x' }))
    const plan = await planBatch(ai, { batchId: 'BAT-03', cars: 1 }, () => true)
    expect(plan.source).toBe('offline')
    expect(plan.caseIds).toEqual(['wc-blue-red'])
  })
})
