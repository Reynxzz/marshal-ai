// Flow 4 — Functional check on the test track. Each routine step is judged by the camera
// and (where relevant) by telemetry; the two must agree for a PASS.

import type { InspectionResult, Verdict } from '../records'
import type { CarSnapshot } from '../vehicle'
import type { FlowHost } from './host'

const CAMERA_STEPS = new Set([
  'headlights_on',
  'headlights_off',
  'brake_lights',
  'reverse',
  'brake_test',
])
const TELEMETRY_STEPS = new Set(['brake_test'])

export type StepDecision = {
  step: string
  worksCamera: boolean
  worksTelemetry: boolean
  verdict: Verdict
  confidence: number
}

export function telemetryWorks(step: string, telemetry: Record<string, unknown>): boolean {
  if (step === 'brake_test') {
    const d = Number(telemetry.brakeDistanceM)
    return Number.isFinite(d) ? d <= 20 : true
  }
  return true
}

export function stepVerdict(step: string, worksCamera: boolean, worksTelemetry: boolean): Verdict {
  const useCamera = CAMERA_STEPS.has(step)
  const useTelemetry = TELEMETRY_STEPS.has(step)
  if (useCamera && useTelemetry) {
    if (worksCamera && worksTelemetry) return 'PASS'
    if (!worksCamera && !worksTelemetry) return 'FAIL'
    return 'REVIEW'
  }
  if (useCamera) return worksCamera ? 'PASS' : 'FAIL'
  if (useTelemetry) return worksTelemetry ? 'PASS' : 'FAIL'
  return 'PASS'
}

export class FunctionalChecker {
  private decisions: StepDecision[] = []
  constructor(private readonly host: FlowHost) {}

  reset(): void {
    this.decisions = []
  }

  async decideStep(
    car: CarSnapshot,
    step: string,
    telemetry: Record<string, unknown>,
  ): Promise<StepDecision> {
    const frame = await this.host.capture.track(car, step)
    const cam = await this.host.ai('track.decide', { frame, step })
    const worksTelemetry = telemetryWorks(step, telemetry)
    const verdict = stepVerdict(step, cam.works, worksTelemetry)
    const decision: StepDecision = {
      step,
      worksCamera: cam.works,
      worksTelemetry,
      verdict,
      confidence: cam.confidence,
    }
    this.decisions.push(decision)
    this.host.emit({
      flow: 4,
      source: 'gemini.functional',
      title: `Track ${verdict}`,
      detail: `vin=${car.vin} step=${step} camera=${cam.works} telemetry=${worksTelemetry} confidence=${cam.confidence.toFixed(2)}`,
      level: verdict === 'PASS' ? 'good' : verdict === 'FAIL' ? 'bad' : 'warn',
      payload: { vin: car.vin, step, verdict },
    })
    return decision
  }

  result(vin: string): InspectionResult {
    const failed = this.decisions.filter((d) => d.verdict === 'FAIL').map((d) => d.step)
    const review = this.decisions.filter((d) => d.verdict === 'REVIEW').map((d) => d.step)
    const verdict: Verdict = failed.length ? 'FAIL' : review.length ? 'REVIEW' : 'PASS'
    const confidence = this.decisions.length
      ? Math.min(...this.decisions.map((d) => d.confidence))
      : 1
    return {
      vin,
      kind: 'functional',
      verdict,
      findings: failed.map((s) => ({ kind: `step failed: ${s}` })),
      mismatches: [],
      confidence,
    }
  }
}
