import { describe, expect, it } from 'vitest'
import { offlineAi } from '#/ai/offline'
import { truthCapture } from '#/ai/truth'
import { SimEngine } from '#/sim/engine'
import type { FlowHost } from '#/sim/flows/host'
import { searchAdversarial } from '#/sim/lab/adversarial'
import { dryRun, generateScenario } from '#/sim/lab/generator'
import { runLab } from '#/sim/lab/index'
import { validateSpec } from '#/sim/lab/spec'
import type { Scenario } from '#/sim/records'
import { makeRng } from '#/sim/rng'
import { world } from '#/sim/world'

function makeHost(): FlowHost {
  return {
    engine: new SimEngine(world, { seed: 1 }),
    world,
    ai: offlineAi,
    capture: truthCapture,
    rng: makeRng(1),
    batchId: 'BAT-01',
    emit: () => {},
  }
}

const sampleSpec: Scenario = {
  id: 'S-TEST',
  request: 'box blocks the lane',
  zone: 'lane',
  actors: [{ kind: 'box', at: 'lane_c', offsetM: 3 }],
  timeOfDay: 'day',
  metrics: ['contact', 'time_to_clear_s', 'min_gap_to_person_m'],
  expect: { clearsWithinS: 90, noContact: true },
}

describe('lab flows 6 + 7', () => {
  it('reports not_triggered for both flows without an incident', async () => {
    const result = await runLab({ host: makeHost(), incident: false, seed: 1 })
    expect(result.flow6).toEqual({ testRequests: 0, generated: 0, status: 'not_triggered' })
    expect(result.flow7).toEqual({ runs: 0, failures: 0, status: 'not_triggered' })
    expect(result.runs).toEqual([])
  })

  it('generates a valid spec and dry-runs finite metrics', async () => {
    const host = makeHost()
    const generated = await generateScenario(host, 'a box and a worker block the lane')
    expect(generated.status).toBe('ok')
    expect(validateSpec(generated.spec, world).ok).toBe(true)

    const dry = await dryRun(host, generated.spec)
    expect(Number.isFinite(dry.metrics.time_to_clear_s)).toBe(true)
    expect(Number.isFinite(dry.metrics.contact)).toBe(true)
    expect(Number.isFinite(dry.metrics.min_gap_to_person_m)).toBe(true)
  })

  it('searches adversarial variants deterministically within budget', async () => {
    const host = makeHost()
    const generated = await generateScenario(host, 'a box and a worker block the lane')

    const a = await searchAdversarial(host, generated.spec, { budget: 4 })
    const b = await searchAdversarial(makeHost(), generated.spec, { budget: 4 })

    expect(a.runs.length).toBeGreaterThan(0)
    expect(a.runs.length).toBeLessThanOrEqual(4)
    for (const run of a.runs) expect(Array.isArray(run.failedCriteria)).toBe(true)
    expect(a.runs).toEqual(b.runs)
    expect(a.found).toBe(true)
    expect(a.minimal).toBeDefined()
  })

  it('rejects an actor placed at an unknown point', () => {
    const bad: Scenario = {
      ...sampleSpec,
      actors: [{ kind: 'box', at: 'nope', offsetM: 3 }],
    }
    const check = validateSpec(bad, world)
    expect(check.ok).toBe(false)
    expect(check.errors.some((e) => e.includes('nope'))).toBe(true)
  })
})
