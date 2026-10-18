// Flow 7 — Adversarial search: propose limited variants of a scenario, run each in its own
// engine, judge from metrics, shrink the first failure to a minimal variant. AI only
// proposes and explains; pass/fail comes from judge.ts.

import type { FlowHost } from '#/sim/flows/host'
import { dryRun, simulateSpec } from '#/sim/lab/generator'
import { judgeRun } from '#/sim/lab/judge'
import { validateSpec } from '#/sim/lab/spec'
import type { Scenario, TestRun } from '#/sim/records'

export const DEFAULT_BUDGET = 4
export const MAX_BUDGET = 24

export type Variation = { offsetM?: number; timeOfDay?: string; workerInLane?: boolean }
export type AdversarialResult = { runs: TestRun[]; minimal?: Scenario; found: boolean }
export type AdversarialReport = {
  explanation: string
  likelyCause: string
  suggestedFix: string
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n))
}

export function buildVariant(spec: Scenario, variation: Variation, index: number): Scenario {
  const baseOffset = spec.actors[0]?.offsetM ?? 3
  const offsetM = clamp(Number(variation.offsetM ?? baseOffset), 0, 10)
  const timeOfDay = variation.timeOfDay === 'night' ? 'night' : 'day'

  let actors = spec.actors.map((a) => ({ ...a, offsetM }))
  if (variation.workerInLane === false) {
    actors = actors.filter((a) => a.kind !== 'walker')
  } else if (variation.workerInLane === true && !actors.some((a) => a.kind === 'walker')) {
    actors.push({ kind: 'walker', at: actors[0]?.at ?? spec.actors[0]?.at ?? 'lane_c', offsetM })
  }

  return { ...spec, id: `${spec.id}-v${index}`, timeOfDay, actors }
}

/** Try lighter variants (day, fewer actors, bigger offset); keep the last that still fails. */
function shrinkFailure(host: FlowHost, variant: Scenario): Scenario {
  const withoutWalkers: Scenario = {
    ...variant,
    actors: variant.actors.filter((a) => a.kind !== 'walker'),
  }
  const candidates: Scenario[] = [
    withoutWalkers,
    { ...variant, timeOfDay: 'day' },
    { ...withoutWalkers, timeOfDay: 'day' },
    {
      ...variant,
      actors: variant.actors.map((a) => ({ ...a, offsetM: clamp(a.offsetM + 5, 0, 10) })),
    },
  ]

  let best = variant
  for (const candidate of candidates) {
    if (candidate.actors.length === 0) continue
    if (!validateSpec(candidate, host.world).ok) continue
    const result = simulateSpec(host, candidate)
    if (!judgeRun(result.metrics, candidate).passed) best = candidate
  }
  return best
}

export async function searchAdversarial(
  host: FlowHost,
  spec: Scenario,
  opts: { budget?: number } = {},
): Promise<AdversarialResult> {
  const budget = Math.max(0, Math.min(opts.budget ?? DEFAULT_BUDGET, MAX_BUDGET))
  const history: TestRun[] = []
  const runs: TestRun[] = []
  let minimal: Scenario | undefined
  let found = false

  while (runs.length < budget) {
    const proposal = await host.ai('adversarial.propose', {
      history,
      metrics: spec.metrics,
    })
    const variations = (proposal.variations as Variation[]).slice(0, 4)
    if (variations.length === 0) break

    for (const variation of variations) {
      if (runs.length >= budget) break
      const variant = buildVariant(spec, variation, runs.length)
      if (!validateSpec(variant, host.world).ok) continue

      const result = await dryRun(host, variant)
      const judged = judgeRun(result.metrics, variant)
      const run: TestRun = {
        scenarioId: variant.id,
        variant,
        metrics: result.metrics,
        passed: judged.passed,
        failedCriteria: judged.failedCriteria,
      }
      runs.push(run)
      history.push(run)

      host.emit({
        flow: 7,
        source: 'gemini.adversarial',
        title: judged.passed ? 'lab.variant_held' : 'lab.variant_failed',
        detail: `scenarioId=${variant.id} passed=${judged.passed} failed=${judged.failedCriteria.join(',') || '-'} metrics=${JSON.stringify(run.metrics)}`,
        level: judged.passed ? 'good' : 'bad',
        payload: run,
      })

      if (!judged.passed && !found) {
        found = true
        minimal = shrinkFailure(host, variant)
        host.emit({
          flow: 7,
          source: 'adversarial',
          title: 'lab.minimal_failure',
          detail: `scenarioId=${minimal.id} actors=${minimal.actors.length} timeOfDay=${minimal.timeOfDay}`,
          level: 'warn',
          payload: minimal,
        })
      }
    }
  }

  host.emit({
    flow: 7,
    source: 'adversarial',
    title: 'lab.search_complete',
    detail: `runs=${runs.length} found=${found} minimal=${minimal?.id ?? '-'}`,
    level: found ? 'warn' : 'good',
    payload: { runs: runs.length, found },
  })
  return { runs, minimal, found }
}

export async function reportAdversarial(
  host: FlowHost,
  run: TestRun,
  metrics: Record<string, number>,
): Promise<AdversarialReport> {
  const res = await host.ai('adversarial.report', { run, metrics })
  return {
    explanation: res.explanation,
    likelyCause: res.likelyCause,
    suggestedFix: res.suggestedFix,
  }
}
