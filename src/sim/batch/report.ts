// Batch report assembly (T14). Pure function: everything it needs is passed in, so the
// KPI numbers are tested from fixed fixtures.

import type { BatchFeedback } from '#/ai/tasks'
import type { BatchEvent, FlowCounts } from '../records'

export type LabelOutcome = { vin: string; expected: string; caught: boolean }
export type LabelSummary = {
  total: number
  caught: number
  missed: number
  falseAlarms: number
  cars: LabelOutcome[]
}

export type BatchReport = {
  batchId: string
  status: string
  generatedAt: string
  model: string
  batchSize: number
  counts: {
    requested: number
    created: number
    finished: number
    shipped: number
    rework: number
    exceptions: number
  }
  durationS: number
  flowKpis: FlowCounts
  labels: LabelSummary
  gemini: BatchFeedback & { degraded: boolean; error?: string }
  log: { events: BatchEvent[]; exceptions: BatchEvent[] }
}

export function buildReport(input: {
  batchId: string
  status: string
  generatedAt: string
  model: string
  createdCars: number
  requestedCars: number
  finishedCars: number
  durationS: number
  counts: FlowCounts
  labels: LabelSummary
  gemini: BatchFeedback & { degraded: boolean; error?: string }
  events: BatchEvent[]
}): BatchReport {
  const exceptions = input.events.filter((e) => e.level === 'warn' || e.level === 'bad')
  const logEvents = input.events.map(stripPayload)
  return {
    batchId: input.batchId,
    status: input.status,
    generatedAt: input.generatedAt,
    model: input.model,
    batchSize: input.createdCars,
    counts: {
      requested: input.requestedCars,
      created: input.createdCars,
      finished: input.finishedCars,
      shipped: input.counts.flow1.shipped,
      rework: input.counts.flow5.rework,
      exceptions: exceptions.length,
    },
    durationS: Math.round(input.durationS),
    flowKpis: input.counts,
    labels: input.labels,
    gemini: input.gemini,
    log: { events: logEvents, exceptions: exceptions.map(stripPayload) },
  }
}

/** Never keep base64 media in stored/exported events. */
function stripPayload(event: BatchEvent): BatchEvent {
  if (!event.payload) return event
  return { ...event, payload: strip(event.payload) }
}

function strip(value: unknown): unknown {
  if (typeof value === 'string') return value.startsWith('data:') ? '[media]' : value
  if (Array.isArray(value)) return value.map(strip)
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) out[k] = strip(v)
    return out
  }
  return value
}
