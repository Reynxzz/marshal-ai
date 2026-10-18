// Flow 5 — Merge inspection verdicts and write the rework summary.

import type { Verdict } from '../records'
import type { FlowHost } from './host'

/** FAIL wins, then REVIEW, then PASS. */
export function mergeVerdicts(...verdicts: Verdict[]): Verdict {
  if (verdicts.includes('FAIL')) return 'FAIL'
  if (verdicts.includes('REVIEW')) return 'REVIEW'
  return 'PASS'
}

export class ReportRouter {
  constructor(private readonly host: FlowHost) {}

  write(findings: string[], bays: string[]): Promise<{ summary: string; steps: string[] }> {
    return this.host.ai('report.write', { findings, bays })
  }
}
