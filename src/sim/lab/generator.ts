// Flow 6 — Generate a scenario spec from an incident request, validate it (up to 3 tries),
// then dry-run it in a throwaway engine to collect metrics. Never touches host.engine.

import { FIXED_DT, SimEngine } from '#/sim/engine'
import type { FlowHost } from '#/sim/flows/host'
import { compileSpec } from '#/sim/lab/compile'
import { judgeRun } from '#/sim/lab/judge'
import { validateSpec } from '#/sim/lab/spec'
import type { Scenario } from '#/sim/records'
import type { World } from '#/sim/world'

export const MAX_ATTEMPTS = 3
export const DRY_RUN_CAP_S = 120
export const CONTACT_M = 2
export const NO_PERSON_GAP_M = 999
const EGO_VIN = 'lab.ego'

export type GenerateResult = { spec: Scenario; attempts: number; status: 'ok' | 'failed' }
export type DryRunResult = {
  metrics: Record<string, number>
  status: 'dry_run_ok' | 'dry_run_failed'
}

function seedFrom(id: string): number {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (Math.imul(31, h) + id.charCodeAt(i)) | 0
  return Math.abs(h) % 100000
}

function nextPoint(world: World, at: string): string {
  const seg = world.segments.find((s) => s.from === at)
  return seg ? seg.to : at
}

export async function generateScenario(host: FlowHost, request: string): Promise<GenerateResult> {
  let spec = await host.ai('scenario.spec', { request })
  let attempts = 1

  while (attempts <= MAX_ATTEMPTS) {
    const check = validateSpec(spec, host.world)
    if (check.ok) {
      host.emit({
        flow: 6,
        source: 'gemini.scenario',
        title: 'lab.generated',
        detail: `scenarioId=${spec.id} attempts=${attempts} status=ok`,
        level: 'good',
        payload: { scenarioId: spec.id, attempts, status: 'ok' },
      })
      return { spec, attempts, status: 'ok' }
    }
    if (attempts >= MAX_ATTEMPTS) {
      host.emit({
        flow: 6,
        source: 'gemini.scenario',
        title: 'lab.generated',
        detail: `scenarioId=${spec.id} attempts=${attempts} status=failed errors=${check.errors.join('; ')}`,
        level: 'bad',
        payload: { scenarioId: spec.id, attempts, status: 'failed' },
      })
      return { spec, attempts, status: 'failed' }
    }
    const prompt = `${request}\nFix these validation errors: ${check.errors.join('; ')}`
    spec = await host.ai('scenario.spec', { request: prompt })
    attempts++
  }

  return { spec, attempts, status: 'failed' }
}

export function simulateSpec(host: FlowHost, spec: Scenario): DryRunResult {
  const setup = compileSpec(spec, host.world)
  const engine = new SimEngine(host.world, { seed: seedFrom(spec.id) })

  const target = spec.actors[0]?.at ?? setup.egoStart
  const neighbor = nextPoint(host.world, target)
  const waypoints = neighbor === target ? [target] : [target, neighbor]

  engine.spawn(EGO_VIN, setup.egoStart)
  engine.send({
    type: 'go',
    vin: EGO_VIN,
    mission: { id: EGO_VIN, waypoints, speedKmh: 30 },
  })
  spec.actors.forEach((actor, i) => {
    if (actor.kind === 'vehicle') engine.spawn(`actor.${i}`, actor.at)
  })

  const walkers = setup.actors.filter((a) => a.kind === 'walker')
  let contact = 0
  let minGap = NO_PERSON_GAP_M
  let clearAt: number | undefined

  const maxSteps = Math.ceil(DRY_RUN_CAP_S / FIXED_DT)
  for (let i = 0; i < maxSteps; i++) {
    engine.step(FIXED_DT)
    const ego = engine.get(EGO_VIN)
    if (!ego) break
    const pos = ego.position()
    for (const actor of setup.actors) {
      const d = Math.hypot(pos.x - actor.x, pos.y - actor.y)
      if (d < CONTACT_M) contact = 1
    }
    for (const w of walkers) {
      const d = Math.hypot(pos.x - w.x, pos.y - w.y)
      if (d < minGap) minGap = d
    }
    if (ego.state === 'stopped') {
      clearAt = engine.simTime
      break
    }
  }

  const metrics: Record<string, number> = {
    contact,
    time_to_clear_s: clearAt ?? DRY_RUN_CAP_S,
    min_gap_to_person_m: walkers.length ? minGap : NO_PERSON_GAP_M,
  }
  const status = judgeRun(metrics, spec).passed ? 'dry_run_ok' : 'dry_run_failed'
  return { metrics, status }
}

export async function dryRun(host: FlowHost, spec: Scenario): Promise<DryRunResult> {
  return simulateSpec(host, spec)
}
