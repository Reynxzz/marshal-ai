// Flow 2 — Remote assistance. Ranks options via AI, enforces the safety rules in code,
// escalates after 60 simulated seconds, and auto-decides headless so a batch never hangs.

import type { AiFrame } from '#/ai/tasks'
import type { AssistanceCase } from '../records'
import type { FlowHost } from './host'

export const ESCALATION_S = 60
export const AUTO_DECIDE_S = 90
export const OPTIONS = ['wait_for_removal', 'reroute', 'manual_override'] as const

export type Decision = { caseId: string; vin: string; option: string; by: string; auto: boolean }

export class AssistanceDesk {
  readonly cases: AssistanceCase[] = []
  private readonly openedAt = new Map<string, number>()
  private readonly decided = new Map<string, Decision>()

  constructor(
    private readonly host: FlowHost,
    private readonly hasAlternative: () => boolean = () => true,
    private readonly autoDecideS: number = AUTO_DECIDE_S,
  ) {}

  async assess(vin: string, situation: string, frame?: AiFrame): Promise<AssistanceCase> {
    const res = await this.host.ai('assist.assess', {
      situation,
      options: [...OPTIONS],
      frame,
      description: situation,
    })

    let ranked = res.ranked.filter((o) => (OPTIONS as readonly string[]).includes(o))
    // Safety rules live in code, never in the model.
    if (!res.peopleOrEquipment) ranked = ranked.filter((o) => o !== 'wait_for_removal')
    if (!this.hasAlternative()) ranked = ranked.filter((o) => o !== 'reroute')
    const recommended = ranked[0] ?? 'manual_override'

    const c: AssistanceCase = {
      caseId: `${this.host.batchId}-CASE${this.cases.length + 1}`,
      vin,
      situation,
      options: [...OPTIONS],
      recommended,
      risk: res.risk,
      peopleOrEquipment: res.peopleOrEquipment,
      status: 'open',
      openedAt: this.host.engine.simTime,
    }
    this.cases.push(c)
    this.openedAt.set(c.caseId, c.openedAt)
    this.host.emit({
      flow: 2,
      source: 'gemini.assist',
      title: 'Assistance requested',
      detail: `vin=${vin} recommended=${recommended} risk=${res.risk} people=${res.peopleOrEquipment}`,
      level: 'warn',
      payload: { case: c },
    })
    return c
  }

  decide(caseId: string, option: string, by: string): Decision | null {
    const c = this.cases.find((x) => x.caseId === caseId)
    if (!c || c.status === 'resolved' || c.status === 'failed') return null
    c.status = 'resolved'
    c.choice = option
    c.decidedBy = by
    c.resolvedAt = this.host.engine.simTime
    const d: Decision = { caseId, vin: c.vin, option, by, auto: false }
    this.decided.set(caseId, d)
    this.host.emit({
      flow: 2,
      source: 'assist',
      title: 'Assist decided',
      detail: `vin=${c.vin} option=${option} by=${by}`,
      level: 'good',
    })
    return d
  }

  /** Escalation + headless auto-decision. Decisions are collected via drainDecisions(). */
  tick(simTime: number): void {
    for (const c of this.cases) {
      if (c.status === 'resolved' || c.status === 'failed') continue
      const age = simTime - (this.openedAt.get(c.caseId) ?? simTime)
      if (age >= ESCALATION_S && c.status !== 'escalated') {
        c.status = 'escalated'
        this.host.emit({
          flow: 2,
          source: 'assist',
          title: 'Assist escalation',
          detail: `vin=${c.vin} no supervisor decision after ${ESCALATION_S}s`,
          level: 'warn',
        })
      }
      if (age >= this.autoDecideS) {
        c.status = 'resolved'
        c.choice = c.recommended
        c.decidedBy = 'auto'
        c.resolvedAt = simTime
        this.decided.set(c.caseId, {
          caseId: c.caseId,
          vin: c.vin,
          option: c.recommended,
          by: 'auto',
          auto: true,
        })
        this.host.emit({
          flow: 2,
          source: 'assist',
          title: 'Assist auto-decided',
          detail: `vin=${c.vin} option=${c.recommended} (headless)`,
          level: 'warn',
        })
      }
    }
  }

  drainDecisions(): Decision[] {
    const out = [...this.decided.values()]
    this.decided.clear()
    return out
  }
}
