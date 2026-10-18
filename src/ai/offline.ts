// Deterministic offline AI: every task in the registry has a handler here, so the whole
// app works (and tests run) without a Gemini key. Answers are derived from inputs only.

import { sampleCaseIds } from '#/sim/batch/cases'
import type { FlowCounts } from '#/sim/records'
import { makeRng } from '#/sim/rng'
import type { AiTaskFn, AiTaskInput, AiTaskName, AiTaskOutput, BatchFeedback } from './tasks'

const FLOW_NAMES = [
  'Yard',
  'Assistance',
  'Visual',
  'Functional',
  'Report',
  'Scenario',
  'Adversarial',
]

export type FeedbackDigest = {
  batchId: string
  batchSize: number
  counts: FlowCounts
  labels?: { total: number; caught: number; missed: number; falseAlarms: number }
  exceptions?: string[]
}

type Truth = {
  colour?: string
  headlights?: boolean
  brake_lights?: boolean
  workers?: number
}

export function parseTruth(truth?: string): Truth {
  const out: Truth = {}
  if (!truth) return out
  for (const part of truth.split(';')) {
    const [rawKey, rawVal] = part.split('=')
    const key = rawKey?.trim()
    const val = rawVal?.trim()
    if (!key || val === undefined) continue
    if (key === 'colour') out.colour = val
    else if (key === 'headlights') out.headlights = val === 'on'
    else if (key === 'brake_lights') out.brake_lights = val === 'on'
    else if (key === 'workers') out.workers = Number(val)
  }
  return out
}

export function offlineTask<K extends AiTaskName>(
  task: K,
  payload: AiTaskInput<K>,
): AiTaskOutput<K> {
  const result = compute(task, payload as Record<string, any>)
  return result as unknown as AiTaskOutput<K>
}

function compute(task: AiTaskName, payload: Record<string, any>): unknown {
  switch (task) {
    case 'visual.screen': {
      const t = parseTruth(payload.frame?.truth)
      const hasDefect = false
      void t
      return { hasDefect, confidence: 0.95 }
    }
    case 'visual.defects':
      return { findings: [] }
    case 'visual.match': {
      const t = parseTruth(payload.frame?.truth)
      const declared = payload.buildSheet?.colour
      if (t.colour && declared && t.colour !== declared) {
        return {
          mismatches: [{ field: 'colour', declared, seen: t.colour }],
          confidence: 0.94,
        }
      }
      return { mismatches: [], confidence: 0.9 }
    }
    case 'track.decide': {
      const t = parseTruth(payload.frame?.truth)
      const step = String(payload.step ?? '')
      let works = true
      let confidence = 0.9
      if (step === 'headlights_on') works = t.headlights === true
      else if (step === 'headlights_off') works = t.headlights === false
      else if (step === 'brake_lights' || step === 'brake_test') works = t.brake_lights === true
      else if (step === 'reverse') confidence = 0.8
      if (t.headlights === undefined && t.brake_lights === undefined) confidence = 0.5
      return { works, confidence }
    }
    case 'assist.assess': {
      const options: string[] = payload.options ?? []
      const people = /worker|person|people|operator|technician/i.test(
        `${payload.description ?? ''} ${payload.situation ?? ''}`,
      )
      const ranked = [...options]
      if (!people) {
        const i = ranked.indexOf('wait_for_removal')
        if (i >= 0) ranked.splice(i, 1)
      }
      const recommended = ranked[0] ?? options[0] ?? 'manual_override'
      return {
        ranked,
        recommended,
        risk: people ? 'medium' : 'low',
        peopleOrEquipment: people,
      }
    }
    case 'yard.exception': {
      const yard = payload.yard as { freeSlots?: string[] } | undefined
      return {
        slots: yard?.freeSlots ?? [],
        reason: 'Reassign to the nearest free slot to keep the truck cycle short.',
      }
    }
    case 'report.write': {
      const findings: string[] = payload.findings ?? []
      const bays: string[] = payload.bays ?? []
      return {
        summary: findings.length
          ? `Rework required: ${findings.join(', ')}.`
          : 'No rework required.',
        steps: bays.map((b) => `Route car to ${b} and reinspect.`),
      }
    }
    case 'scenario.spec': {
      const request = String(payload.request ?? 'box blocks the lane')
      return {
        id: `S-${hash(request)}`,
        request,
        zone: 'lane',
        actors: [
          { kind: 'box', at: 'lane_c', offsetM: 5 },
          { kind: 'walker', at: 'lane_c', offsetM: 2 },
        ],
        timeOfDay: 'day',
        metrics: ['min_gap_to_person_m', 'time_to_clear_s', 'contact'],
        expect: { clearsWithinS: 90, noContact: true },
      }
    }
    case 'adversarial.propose': {
      const history = (payload.history ?? []) as unknown[]
      const n = history.length
      return {
        variations: [0, 1, 2, 3].map((i) => ({
          offsetM: 3 + (n + i) * 1.5,
          timeOfDay: (n + i) % 2 === 0 ? 'day' : 'night',
          workerInLane: i % 2 === 1,
        })),
        reasoning: `Ladder step ${n}: widen offset, alternate night, place worker in lane.`,
      }
    }
    case 'adversarial.report': {
      const run = payload.run as { passed?: boolean; failedCriteria?: string[] } | undefined
      const failed = run?.failedCriteria ?? []
      return {
        explanation: run?.passed
          ? 'Variant held the safety envelope.'
          : `Variant failed criteria: ${failed.join(', ') || 'unknown'}.`,
        likelyCause: failed.length
          ? 'Actor placed inside the braking envelope at the tested offset.'
          : 'No failure observed.',
        suggestedFix: 'Increase the minimum approach gap or slow the ego near the incident.',
      }
    }
    case 'batch.plan':
      return {
        caseIds: sampleCaseIds(makeRng(Number(payload.seed ?? 1)), Number(payload.cars ?? 1)),
        rationale: 'Deterministic offline pick: seeded sample of the case library.',
      }
    case 'batch.feedback':
      return offlineFeedback(payload.digest as FeedbackDigest)
    default:
      return {}
  }
}

function flowEntry(flow: number, observation: string, issues: string[], recommendation: string) {
  return { flow, name: FLOW_NAMES[flow - 1] ?? `Flow ${flow}`, observation, issues, recommendation }
}

export function offlineFeedback(digest: FeedbackDigest): BatchFeedback {
  const c = digest.counts
  const exceptions = digest.exceptions ?? []
  const perFlow: BatchFeedback['perFlow'] = [
    flowEntry(
      1,
      `Released ${c.flow1.released}, in yard ${c.flow1.inYard}, shipped ${c.flow1.shipped}.`,
      c.flow1.waiting ? [`${c.flow1.waiting} waiting`] : [],
      'Keep gate spacing as configured.',
    ),
    flowEntry(
      2,
      c.flow2.cases ? `${c.flow2.cases} assistance case(s).` : 'Not triggered (no incident).',
      c.flow2.escalated ? [`${c.flow2.escalated} escalated`] : [],
      'Review escalation thresholds.',
    ),
    flowEntry(
      3,
      `Visual pass ${c.flow3.pass}, review ${c.flow3.review}, fail ${c.flow3.fail}.`,
      c.flow3.fail ? [`${c.flow3.fail} failed`] : [],
      'Investigate failed colour matches.',
    ),
    flowEntry(
      4,
      `Functional pass ${c.flow4.pass}, fail ${c.flow4.fail}, disagreements ${c.flow4.disagreements}.`,
      c.flow4.fail ? [`${c.flow4.fail} failed`] : [],
      'Check lamp circuits on the track.',
    ),
    flowEntry(
      5,
      `Report pass ${c.flow5.pass}, rework ${c.flow5.rework}, shipped ${c.flow5.shipped}.`,
      [],
      'Track rework rate over time.',
    ),
    flowEntry(
      6,
      c.flow6.status === 'not_triggered'
        ? 'Not triggered (no incident).'
        : `Generated ${c.flow6.generated} scenario(s).`,
      [],
      'Broaden scenario library.',
    ),
    flowEntry(
      7,
      c.flow7.status === 'not_triggered'
        ? 'Not triggered (no incident).'
        : `Runs ${c.flow7.runs}, failures ${c.flow7.failures}.`,
      c.flow7.failures ? [`${c.flow7.failures} fragile variants`] : [],
      'Promote failing variants to regression suite.',
    ),
  ]
  const topIssues: string[] = []
  if (c.flow3.fail) topIssues.push(`${c.flow3.fail} car(s) failed visual inspection.`)
  if (c.flow4.fail) topIssues.push(`${c.flow4.fail} car(s) failed functional checks.`)
  if (c.flow7.failures) topIssues.push(`${c.flow7.failures} adversarial variant(s) failed.`)
  if (exceptions.length) topIssues.push(`${exceptions.length} exception event(s) logged.`)

  const risk: BatchFeedback['risk'] = c.flow7.failures
    ? 'high'
    : c.flow3.fail || c.flow4.fail
      ? 'medium'
      : 'low'

  return {
    batchSize: digest.batchSize,
    summary: `Batch ${digest.batchId}: ${c.flow5.shipped} shipped, ${c.flow5.rework} reworked. ${topIssues.length} issue(s) flagged.`,
    perFlow,
    topIssues,
    recommendations: [
      'Verify lamp circuits for cars flagged on the track.',
      'Recheck paint colour matching against build sheets.',
    ],
    risk,
  }
}

function hash(input: string): number {
  let h = 0
  for (let i = 0; i < input.length; i++) h = (Math.imul(31, h) + input.charCodeAt(i)) | 0
  return Math.abs(h) % 100000
}

export const offlineAi: AiTaskFn = async (task, payload) => offlineTask(task, payload)
