// The single registry of AI tasks: zod input/output schemas + the AiTaskFn contract.
// Pure module (zod only) so the simulation can depend on the contract without pulling
// in any SDK or React. The server implementation lives in server.ts (T10).

import { z } from 'zod'
import {
  buildSheetSchema,
  findingSchema,
  labelSchema,
  mismatchSchema,
  scenarioSchema,
  testRunSchema,
} from '#/sim/records'

export const frameSchema = z.object({
  view: z.string(),
  mime: z.string(),
  dataUrl: z.string(),
  width: z.number(),
  height: z.number(),
  /** Truth description used offline / when WebGL is unavailable (T05). */
  truth: z.string().optional(),
})
export type AiFrame = z.infer<typeof frameSchema>

export const batchFeedbackSchema = z.object({
  batchSize: z.number(),
  summary: z.string(),
  perFlow: z.array(
    z.object({
      flow: z.number(),
      name: z.string(),
      observation: z.string(),
      issues: z.array(z.string()),
      recommendation: z.string(),
    }),
  ),
  topIssues: z.array(z.string()),
  recommendations: z.array(z.string()),
  risk: z.enum(['low', 'medium', 'high']),
})
export type BatchFeedback = z.infer<typeof batchFeedbackSchema>

export const aiTasks = {
  'visual.screen': {
    input: z.object({ frame: frameSchema }),
    output: z.object({ hasDefect: z.boolean(), confidence: z.number() }),
  },
  'visual.defects': {
    input: z.object({ frame: frameSchema }),
    output: z.object({ findings: z.array(findingSchema) }),
  },
  'visual.match': {
    input: z.object({ frame: frameSchema, buildSheet: buildSheetSchema }),
    output: z.object({ mismatches: z.array(mismatchSchema), confidence: z.number() }),
  },
  'track.decide': {
    input: z.object({ frame: frameSchema, step: z.string() }),
    output: z.object({ works: z.boolean(), confidence: z.number() }),
  },
  'assist.assess': {
    input: z.object({
      situation: z.string(),
      options: z.array(z.string()),
      frame: frameSchema.optional(),
      description: z.string().optional(),
    }),
    output: z.object({
      ranked: z.array(z.string()),
      recommended: z.string(),
      risk: z.string(),
      peopleOrEquipment: z.boolean(),
    }),
  },
  'yard.exception': {
    input: z.object({ exception: z.string(), yard: z.unknown() }),
    output: z.object({ slots: z.array(z.string()), reason: z.string() }),
  },
  'report.write': {
    input: z.object({ findings: z.array(z.string()), bays: z.array(z.string()) }),
    output: z.object({ summary: z.string(), steps: z.array(z.string()) }),
  },
  'scenario.spec': {
    input: z.object({ request: z.string() }),
    output: scenarioSchema,
  },
  'adversarial.propose': {
    input: z.object({ history: z.array(testRunSchema), metrics: z.array(z.string()) }),
    output: z.object({ variations: z.array(z.unknown()), reasoning: z.string() }),
  },
  'adversarial.report': {
    input: z.object({ run: testRunSchema, metrics: z.record(z.string(), z.number()) }),
    output: z.object({
      explanation: z.string(),
      likelyCause: z.string(),
      suggestedFix: z.string(),
    }),
  },
  'batch.plan': {
    input: z.object({
      cars: z.number().int().min(1).max(6),
      seed: z.number(),
      menu: z.array(z.object({ id: z.string(), label: z.string(), kind: labelSchema })),
    }),
    output: z.object({ caseIds: z.array(z.string()), rationale: z.string() }),
  },
  'batch.feedback': {
    input: z.object({ digest: z.unknown() }),
    output: batchFeedbackSchema,
  },
} as const

export type AiTaskName = keyof typeof aiTasks
export type AiTaskInput<K extends AiTaskName> = z.infer<(typeof aiTasks)[K]['input']>
export type AiTaskOutput<K extends AiTaskName> = z.infer<(typeof aiTasks)[K]['output']>
export type AiTaskFn = <K extends AiTaskName>(
  task: K,
  payload: AiTaskInput<K>,
) => Promise<AiTaskOutput<K>>

export function validateTask<K extends AiTaskName>(task: K, payload: unknown): AiTaskInput<K> {
  return aiTasks[task].input.parse(payload) as AiTaskInput<K>
}

export const AI_TASK_NAMES = Object.keys(aiTasks) as AiTaskName[]
