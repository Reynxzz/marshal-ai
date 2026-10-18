// Flow 3 — Visual inspection at the gate. Screen → defects → build-sheet match, all via
// the AI registry (offline by default). Thresholds are named so they are easy to calibrate.

import type { AiFrame } from '#/ai/tasks'
import type { BuildSheet, Finding, InspectionResult, Mismatch, Verdict } from '../records'
import type { CarSnapshot } from '../vehicle'
import type { FlowHost } from './host'

export const REVIEW_MIN_CONFIDENCE = 0.6
export const FAIL_CONFIDENCE = 0.9

export function visualVerdict(
  mismatches: Mismatch[],
  findings: Finding[],
  confidence: number,
): Verdict {
  if (mismatches.length > 0 && confidence >= REVIEW_MIN_CONFIDENCE) return 'FAIL'
  if (findings.length > 0) return 'REVIEW'
  if (confidence >= REVIEW_MIN_CONFIDENCE && confidence < FAIL_CONFIDENCE) return 'REVIEW'
  return 'PASS'
}

export class VisualInspector {
  constructor(private readonly host: FlowHost) {}

  async inspect(car: CarSnapshot, buildSheet: BuildSheet): Promise<InspectionResult> {
    const frames = await this.host.capture.gate(car)
    const findings: Finding[] = []
    let screenConfidence = 1

    for (const frame of frames) {
      const screen = await this.host.ai('visual.screen', { frame })
      screenConfidence = Math.min(screenConfidence, screen.confidence)
      if (screen.hasDefect) {
        const defects = await this.host.ai('visual.defects', { frame })
        findings.push(...defects.findings)
      }
    }

    const primary = frames[0] as AiFrame
    const match = await this.host.ai('visual.match', { frame: primary, buildSheet })
    const confidence = Math.min(screenConfidence, match.confidence)
    const verdict = visualVerdict(match.mismatches, findings, confidence)

    this.host.emit({
      flow: 3,
      source: 'gemini.visual',
      title: `Visual ${verdict}`,
      detail: `vin=${car.vin} confidence=${confidence.toFixed(2)} findings=${findings.length} mismatches=${match.mismatches.length}`,
      level: verdict === 'PASS' ? 'good' : verdict === 'FAIL' ? 'bad' : 'warn',
      payload: { vin: car.vin, verdict, findings, mismatches: match.mismatches },
    })

    return {
      vin: car.vin,
      kind: 'visual',
      verdict,
      findings,
      mismatches: match.mismatches,
      confidence,
    }
  }
}
