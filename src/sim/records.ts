// All data contracts for the simulation, as zod schemas + inferred TypeScript types.
// Standalone copy — this project never imports code from sibling projects (aturan #1).

import { z } from 'zod'

export const buildColourSchema = z.enum(['blue', 'white', 'grey', 'black'])
export type BuildColour = z.infer<typeof buildColourSchema>

export const carStateSchema = z.enum([
  'line_end',
  'waiting_dispatch',
  'held',
  'inspection_gate',
  'test_track',
  'report',
  'rework',
  'yard',
  'shipped',
])
export type CarState = z.infer<typeof carStateSchema>

export const verdictSchema = z.enum(['PASS', 'REVIEW', 'FAIL'])
export type Verdict = z.infer<typeof verdictSchema>

export const levelSchema = z.enum(['info', 'good', 'warn', 'bad'])
export type Level = z.infer<typeof levelSchema>

export const labelSchema = z.enum(['clean', 'wrong_colour', 'dead_lamp', 'incident'])
export type ExpectedLabel = z.infer<typeof labelSchema>

export const buildSheetSchema = z.object({
  model: z.string(),
  colour: buildColourSchema,
  options: z.array(z.string()),
})
export type BuildSheet = z.infer<typeof buildSheetSchema>

export const carSchema = z.object({
  vin: z.string(),
  buildSheet: buildSheetSchema,
  state: carStateSchema,
  location: z.string(),
  slot: z.string().optional(),
  missionId: z.string().optional(),
  route: z.array(z.string()).optional(),
  spawnColour: z.string().optional(),
})
export type Car = z.infer<typeof carSchema>

export const missionSchema = z.object({
  id: z.string(),
  vin: z.string(),
  waypoints: z.array(z.string()),
  status: z.enum(['queued', 'running', 'done', 'held']),
  routine: z.string().optional(),
  routineSteps: z.array(z.string()).optional(),
})
export type Mission = z.infer<typeof missionSchema>

export const eventSchema = z.object({
  seq: z.number(),
  at: z.number(),
  flow: z.number().min(1).max(7).optional(),
  source: z.string(),
  title: z.string(),
  detail: z.string(),
  level: levelSchema,
  payload: z.unknown().optional(),
})
export type Event = z.infer<typeof eventSchema>

export const batchEventSchema = eventSchema.extend({ batchId: z.string() })
export type BatchEvent = z.infer<typeof batchEventSchema>

export const findingSchema = z.object({
  kind: z.string(),
  zone: z.string().optional(),
  view: z.string().optional(),
})
export type Finding = z.infer<typeof findingSchema>

export const mismatchSchema = z.object({
  field: z.string(),
  declared: z.string(),
  seen: z.string(),
})
export type Mismatch = z.infer<typeof mismatchSchema>

export const assistanceCaseSchema = z.object({
  caseId: z.string(),
  vin: z.string(),
  situation: z.string(),
  options: z.array(z.string()),
  recommended: z.string(),
  risk: z.string(),
  peopleOrEquipment: z.boolean(),
  status: z.enum(['open', 'resolved', 'escalated', 'failed']),
  choice: z.string().optional(),
  decidedBy: z.string().optional(),
  openedAt: z.number(),
  resolvedAt: z.number().optional(),
})
export type AssistanceCase = z.infer<typeof assistanceCaseSchema>

export const inspectionResultSchema = z.object({
  vin: z.string(),
  kind: z.enum(['visual', 'functional']),
  verdict: verdictSchema,
  findings: z.array(findingSchema),
  mismatches: z.array(mismatchSchema),
  confidence: z.number(),
})
export type InspectionResult = z.infer<typeof inspectionResultSchema>

export const carReportSchema = z.object({
  vin: z.string(),
  verdict: verdictSchema,
  summary: z.string(),
  findings: z.array(z.string()),
  failedChecks: z.array(z.string()),
  rework: z.array(z.string()).optional(),
  shipped: z.boolean(),
})
export type CarReport = z.infer<typeof carReportSchema>

export const proposalSchema = z.object({
  id: z.string(),
  kind: z.enum(['replan', 'rework', 'review']),
  summary: z.string(),
  payload: z.unknown(),
  status: z.enum(['pending', 'approved', 'rejected']),
  decidedBy: z.string().optional(),
})
export type Proposal = z.infer<typeof proposalSchema>

export const truckSchema = z.object({
  truckId: z.string(),
  etaMin: z.number(),
  cars: z.array(z.string()),
})
export type Truck = z.infer<typeof truckSchema>

export const incidentBlockerSchema = z.enum(['box', 'walker', 'vehicle'])
export type IncidentBlocker = z.infer<typeof incidentBlockerSchema>

export const expectedSchema = z.object({
  kind: labelSchema,
  faultStep: z.string().optional(),
  /** Case-library id this car was planned from (optional for back-compat). */
  caseId: z.string().optional(),
  /** Incident composition, only for kind `incident`. */
  blockers: z.array(incidentBlockerSchema).optional(),
})
export type Expected = z.infer<typeof expectedSchema>

export const batchCarSchema = z.object({
  vin: z.string(),
  expected: expectedSchema,
  state: carStateSchema,
  stage: z.number().min(1).max(7),
  location: z.string(),
  buildSheet: buildSheetSchema.optional(),
  colour: z.string().optional(),
  slot: z.string().optional(),
  truckId: z.string().optional(),
  gateVerdict: verdictSchema.optional(),
  reportVerdict: verdictSchema.optional(),
})
export type BatchCar = z.infer<typeof batchCarSchema>

export const manifestSchema = z.object({
  batchId: z.string(),
  seed: z.number(),
  cars: z.array(batchCarSchema),
  trucks: z.array(truckSchema),
})
export type Manifest = z.infer<typeof manifestSchema>

export const flowCountsSchema = z.object({
  flow1: z.object({
    released: z.number(),
    waiting: z.number(),
    inYard: z.number(),
    shipped: z.number(),
  }),
  flow2: z.object({
    cases: z.number(),
    open: z.number(),
    resolved: z.number(),
    escalated: z.number(),
  }),
  flow3: z.object({ pass: z.number(), review: z.number(), fail: z.number() }),
  flow4: z.object({ pass: z.number(), fail: z.number(), disagreements: z.number() }),
  flow5: z.object({ pass: z.number(), rework: z.number(), shipped: z.number() }),
  flow6: z.object({
    testRequests: z.number(),
    generated: z.number(),
    status: z.string(),
  }),
  flow7: z.object({ runs: z.number(), failures: z.number(), status: z.string() }),
})
export type FlowCounts = z.infer<typeof flowCountsSchema>

export const scenarioActorSchema = z.object({
  kind: z.enum(['walker', 'box', 'vehicle']),
  at: z.string(),
  offsetM: z.number(),
})
export type ScenarioActor = z.infer<typeof scenarioActorSchema>

export const scenarioSchema = z.object({
  id: z.string(),
  request: z.string(),
  zone: z.string(),
  actors: z.array(scenarioActorSchema),
  timeOfDay: z.enum(['day', 'night']),
  metrics: z.array(z.string()),
  expect: z.object({
    clearsWithinS: z.number().optional(),
    noContact: z.boolean(),
  }),
})
export type Scenario = z.infer<typeof scenarioSchema>

export const testRunSchema = z.object({
  scenarioId: z.string(),
  variant: z.unknown(),
  metrics: z.record(z.string(), z.number()),
  passed: z.boolean(),
  failedCriteria: z.array(z.string()),
})
export type TestRun = z.infer<typeof testRunSchema>

export const batchStatusSchema = z.enum(['queued', 'running', 'done', 'failed', 'cancelled'])
export type BatchStatus = z.infer<typeof batchStatusSchema>

export const batchSchema = z.object({
  batchId: z.string(),
  status: batchStatusSchema,
  requestedCars: z.number(),
  createdCars: z.number(),
  finishedCars: z.number(),
  progress: z.number(),
  seed: z.number(),
  engine: z.object({ timeScale: z.number(), simTimeS: z.number() }),
  incident: z.object({
    enabled: z.boolean(),
    vin: z.string().optional(),
    situation: z.string().optional(),
  }),
  manifest: manifestSchema,
  counts: flowCountsSchema,
  report: z.unknown().optional(),
  error: z.string().optional(),
})
export type Batch = z.infer<typeof batchSchema>
