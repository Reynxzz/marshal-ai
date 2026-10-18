// Flows 6 + 7 orchestration for the lab: generate -> dry run -> adversarial search ->
// report. Only runs when the batch has an incident; failures never abort the batch.

import type { FlowHost } from '#/sim/flows/host'
import { type AdversarialReport, reportAdversarial, searchAdversarial } from '#/sim/lab/adversarial'
import { generateScenario, simulateSpec } from '#/sim/lab/generator'
import { judgeRun } from '#/sim/lab/judge'
import type { Scenario, TestRun } from '#/sim/records'

export type LabInput = { host: FlowHost; incident: boolean; request?: string; seed: number }
export type LabResult = {
  flow6: { testRequests: number; generated: number; status: string }
  flow7: { runs: number; failures: number; status: string }
  scenario?: Scenario
  runs: TestRun[]
  adversarial?: AdversarialReport
}

const DEFAULT_REQUEST = 'A box and a worker block the lane after the inspection gate.'
const LAB_BUDGET = 4

export async function runLab(input: LabInput): Promise<LabResult> {
  const { host, incident } = input

  if (!incident) {
    host.emit({
      flow: 6,
      source: 'lab',
      title: 'lab.not_triggered',
      detail: 'no incident in batch',
      level: 'info',
      payload: { flow6: 'not_triggered', flow7: 'not_triggered' },
    })
    return {
      flow6: { testRequests: 0, generated: 0, status: 'not_triggered' },
      flow7: { runs: 0, failures: 0, status: 'not_triggered' },
      runs: [],
    }
  }

  const request = input.request ?? DEFAULT_REQUEST
  const generated = await generateScenario(host, request)
  if (generated.status === 'failed') {
    host.emit({
      flow: 7,
      source: 'lab',
      title: 'lab.skipped',
      detail: 'scenario generation failed; adversarial skipped',
      level: 'warn',
      payload: { flow7: 'skipped' },
    })
    return {
      flow6: { testRequests: 1, generated: 0, status: 'failed' },
      flow7: { runs: 0, failures: 0, status: 'skipped' },
      runs: [],
    }
  }

  const dry = simulateSpec(host, generated.spec)
  host.emit({
    flow: 6,
    source: 'lab',
    title: 'lab.dry_run',
    detail: `scenarioId=${generated.spec.id} status=${dry.status} metrics=${JSON.stringify(dry.metrics)}`,
    level: dry.status === 'dry_run_ok' ? 'good' : 'warn',
    payload: dry,
  })

  const search = await searchAdversarial(host, generated.spec, { budget: LAB_BUDGET })
  const runs = search.runs
  const failures = runs.filter((r) => !r.passed).length

  const baseline: TestRun = {
    scenarioId: generated.spec.id,
    variant: generated.spec,
    metrics: dry.metrics,
    passed: dry.status === 'dry_run_ok',
    failedCriteria: judgeRun(dry.metrics, generated.spec).failedCriteria,
  }
  const reportTarget = runs.find((r) => !r.passed) ?? runs[0] ?? baseline
  const adversarial = await reportAdversarial(host, reportTarget, reportTarget.metrics)

  host.emit({
    flow: 7,
    source: 'lab',
    title: 'lab.complete',
    detail: `runs=${runs.length} failures=${failures} verdict=${failures ? 'fragile' : 'robust'}`,
    level: failures ? 'warn' : 'good',
    payload: { runs: runs.length, failures },
  })

  return {
    flow6: { testRequests: 1, generated: 1, status: 'ok' },
    flow7: { runs: runs.length, failures, status: 'ok' },
    scenario: generated.spec,
    runs,
    adversarial,
  }
}
