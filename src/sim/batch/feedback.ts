// Feedback digest and normalization (T14). Gemini only *explains* the numbers; KPI maths
// stays in TypeScript.

import { type FeedbackDigest, offlineFeedback } from '#/ai/offline'
import type { AiTaskFn, BatchFeedback } from '#/ai/tasks'
import type { FlowCounts } from '../records'

export const FLOW_NAMES = [
  'Yard',
  'Assistance',
  'Visual',
  'Functional',
  'Report',
  'Scenario',
  'Adversarial',
]

export function buildDigest(input: {
  batchId: string
  batchSize: number
  counts: FlowCounts
  labels: FeedbackDigest['labels']
  exceptions: string[]
}): FeedbackDigest {
  return {
    batchId: input.batchId,
    batchSize: input.batchSize,
    counts: input.counts,
    labels: input.labels,
    exceptions: input.exceptions,
  }
}

/** Fill missing flows, force batchSize == createdCars, normalize risk. */
export function normalizeFeedback(feedback: BatchFeedback, createdCars: number): BatchFeedback {
  const perFlow = feedback.perFlow.slice(0, 7).map((f) => ({ ...f }))
  for (let i = perFlow.length; i < 7; i++) {
    perFlow.push({
      flow: i + 1,
      name: FLOW_NAMES[i] ?? `Flow ${i + 1}`,
      observation: '',
      issues: [],
      recommendation: '',
    })
  }
  const risk = (['low', 'medium', 'high'] as const).includes(feedback.risk) ? feedback.risk : 'low'
  return { ...feedback, batchSize: createdCars, perFlow, risk }
}

export async function buildFeedback(
  ai: AiTaskFn,
  digest: FeedbackDigest,
  createdCars: number,
  degradedHint: boolean,
): Promise<{ feedback: BatchFeedback; degraded: boolean; error?: string }> {
  try {
    const raw = await ai('batch.feedback', { digest })
    return { feedback: normalizeFeedback(raw, createdCars), degraded: degradedHint }
  } catch (e) {
    return {
      feedback: normalizeFeedback(offlineFeedback(digest), createdCars),
      degraded: true,
      error: e instanceof Error ? e.message : String(e),
    }
  }
}
