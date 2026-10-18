import { describe, expect, it } from 'vitest'
import { offlineAi } from '#/ai/offline'
import { truthCapture } from '#/ai/truth'
import { buildManifest } from '../batch/manifest'
import { BatchRunner } from '../batch/orchestrator'
import { FIXED_DT } from '../engine'
import { world } from '../world'

async function runBatch(cars: number, seed = 7, caseIds?: string[]): Promise<BatchRunner> {
  const runner = new BatchRunner({
    batchId: `BAT-0${cars}`,
    cars,
    seed,
    caseIds,
    ai: offlineAi,
    capture: truthCapture,
    world,
    generatedAt: () => '2026-01-01T00:00:00.000Z',
    degraded: () => true,
  })
  runner.start()
  let steps = 0
  while (runner.status === 'running' && steps < 60000) {
    await runner.step(FIXED_DT)
    steps++
  }
  return runner
}

describe('buildManifest', () => {
  it('is deterministic and samples unique cases', () => {
    const a = buildManifest('BAT-01', 3, 7)
    const b = buildManifest('BAT-01', 3, 7)
    expect(a).toEqual(b)
    expect(a.cars).toHaveLength(3)
    expect(new Set(a.cars.map((c) => c.expected.caseId)).size).toBe(3)
    expect(a.cars[0]?.vin).toMatch(/^VIN-B01-1$/)
    expect(a.trucks.length).toBe(2)
  })

  it('honours an explicit case selection', () => {
    const m = buildManifest('BAT-01', 2, 7, ['wc-blue-red', 'clean-white'])
    expect(m.cars[0]?.expected).toEqual({ kind: 'wrong_colour', caseId: 'wc-blue-red' })
    expect(m.cars[0]?.buildSheet?.colour).toBe('blue')
    expect(m.cars[0]?.colour).toBe('#e23b2e')
    expect(m.cars[1]?.expected.kind).toBe('clean')
  })

  it('falls back to a seeded sample for an invalid selection', () => {
    const m = buildManifest('BAT-01', 2, 7, ['nope', 'nope'])
    expect(m.cars).toHaveLength(2)
    expect(m.cars.every((c) => c.expected.caseId)).toBe(true)
  })
})

describe('BatchRunner (offline)', () => {
  it('finishes a 1-car clean batch with a 7-flow report', async () => {
    const runner = await runBatch(1, 7, ['clean-blue'])
    expect(runner.status).toBe('done')
    expect(runner.report).toBeDefined()
    expect(runner.report?.batchSize).toBe(1)
    expect(runner.report?.gemini.perFlow).toHaveLength(7)
    expect(runner.report?.gemini.degraded).toBe(true)
    expect(runner.counts.flow6.status).toBe('not_triggered')
    expect(runner.counts.flow7.status).toBe('not_triggered')
  })

  it('finishes a 3-car batch with an incident, wrong colour and dead lamp', async () => {
    const runner = await runBatch(3, 7, ['wc-blue-red', 'dl-headlights', 'inc-box-worker'])
    expect(runner.status).toBe('done')
    expect(runner.report?.gemini.perFlow).toHaveLength(7)
    expect(runner.counts.flow2.cases).toBeGreaterThanOrEqual(1)
    expect(runner.counts.flow3.fail).toBeGreaterThanOrEqual(1)
    expect(runner.counts.flow4.fail).toBeGreaterThanOrEqual(1)
    expect(runner.counts.flow5.rework).toBeGreaterThanOrEqual(1)
    const labels = runner.report?.labels
    expect(labels?.total).toBe(3)
    expect(labels?.caught).toBeGreaterThanOrEqual(2)
    expect(runner.counts.flow6.status).toBe('ok')
  })

  it('runs a randomly sampled batch to completion', async () => {
    const runner = await runBatch(3, 11)
    expect(runner.status).toBe('done')
    expect(runner.report?.labels?.cars).toHaveLength(3)
  })
})
