// zod 4 input schemas for the Marshal server functions, plus the raw JSON shapes Gemini is asked
// to return. Pure module (zod only). Gemini answers are re-checked against the safety rules in
// src/lib/serverFns/logic.ts before they reach the orchestrator.
//
// `hint` / `hints` mirror the offline truth-hint convention of src/ai/truth.ts
// (e.g. 'colour=red; damage=dent', 'headlights=off', 'worker=1'). They are never sent to Gemini;
// they only drive the deterministic offline answers.

import { z } from 'zod'

// Size limits: the server functions are public, so every list and string is bounded to keep
// Gemini spend and server-side A* CPU per request small.

/** Most images per request (lamp and assist clips send 8). */
export const MAX_FRAMES = 8
/** Longest base64 frame (about 512 KB of JPEG). */
export const MAX_FRAME_CHARS = 700_000
/** Most obstacles per parking step (A* cost grows with every box). */
export const MAX_OBSTACLES = 64
/** Most yard slots in a layout (the grid has 80). */
export const MAX_SLOTS = 100
/** Most VINs or slot ids in a constraint list. */
export const MAX_LIST = 100
/** Most telemetry keys per lamp step. */
export const MAX_TELEMETRY_KEYS = 32
/** Longest id (VIN, slot, case id, step name, colour). */
export const MAX_ID_CHARS = 64
/** Longest free text (reasons, hazard descriptions, hints). */
export const MAX_TEXT_CHARS = 500

const id = () => z.string().max(MAX_ID_CHARS)
const text = () => z.string().max(MAX_TEXT_CHARS)
const frame = () => z.string().max(MAX_FRAME_CHARS)
const frames = () => z.array(frame()).max(MAX_FRAMES)

export const assistOptionSchema = z.enum(['wait_for_removal', 'pass_left_3kmh', 'reroute_lane_c'])
export const routeOptionSchema = z.enum(['finishing_corner', 'lane_c'])
export const gateViewSchema = z.enum(['front', 'rear', 'left', 'right'])

/**
 * Gate photos by view. A view may be missing when its frame was too large to send (the flow
 * drops it client-side); at least one view is required.
 */
export const gateFramesSchema = z
  .object({
    front: frame().optional(),
    rear: frame().optional(),
    left: frame().optional(),
    right: frame().optional(),
  })
  .refine((value) => Object.values(value).some((item) => item !== undefined), {
    message: 'at least one gate view',
  })

export const poseSchema = z.object({ x: z.number(), y: z.number(), yaw: z.number() })

export const obstacleSchema = z.object({
  id: id(),
  x: z.number(),
  y: z.number(),
  yaw: z.number(),
  length: z.number().positive().max(50),
  width: z.number().positive().max(50),
  moving: z.boolean(),
})

export const slotSchema = z.object({
  id: id(),
  x: z.number(),
  y: z.number(),
  yaw: z.number(),
})

export const boundingBoxSchema = z.object({
  label: z.string(),
  x: z.number(),
  y: z.number(),
  w: z.number(),
  h: z.number(),
})

export const yardMoveSchema = z.object({ vin: z.string(), from: z.string(), to: z.string() })

// --- Server function inputs -----------------------------------------------------------------

export const gateInputSchema = z.object({
  vin: id(),
  expectedColour: id().min(1),
  frames: gateFramesSchema,
  hints: z
    .object({
      front: text().optional(),
      rear: text().optional(),
      left: text().optional(),
      right: text().optional(),
    })
    .optional(),
})
export type GateInput = z.infer<typeof gateInputSchema>

export const lampInputSchema = z.object({
  vin: id(),
  /** R-12 step name, e.g. 'headlights'. */
  step: id().min(1),
  telemetry: z
    .record(id(), z.unknown())
    .refine((value) => Object.keys(value).length <= MAX_TELEMETRY_KEYS, {
      message: `at most ${MAX_TELEMETRY_KEYS} telemetry keys`,
    }),
  frames: frames(),
  hint: text().optional(),
})
export type LampInput = z.infer<typeof lampInputSchema>

export const assistInputSchema = z.object({
  case_id: id(),
  reason: text(),
  options: z.array(assistOptionSchema).min(1).max(3),
  frames: frames(),
  hint: text().optional(),
})
export type AssistInput = z.infer<typeof assistInputSchema>

export const parkingInputSchema = z.object({
  vin: id(),
  pose: poseSchema,
  obstacles: z.array(obstacleSchema).max(MAX_OBSTACLES),
  slot: slotSchema,
})
export type ParkingInput = z.infer<typeof parkingInputSchema>

export const routeInputSchema = z.object({
  hazardDescription: text(),
  options: z.array(routeOptionSchema).min(1).max(2),
})
export type RouteInput = z.infer<typeof routeInputSchema>

export const yardInputSchema = z.object({
  /** slot id -> VIN parked there, or null when free. */
  layout: z
    .record(id(), id().nullable())
    .refine((value) => Object.keys(value).length <= MAX_SLOTS, {
      message: `at most ${MAX_SLOTS} yard slots`,
    }),
  constraints: z.object({
    priorityVins: z.array(id()).max(MAX_LIST).optional(),
    reservedSlots: z.array(id()).max(MAX_LIST).optional(),
    reason: text().optional(),
  }),
})
export type YardInput = z.infer<typeof yardInputSchema>

// --- Raw Gemini answers (validated, then safety-checked in code) ------------------------------

export const gateModelSchema = z.object({
  pass: z.boolean(),
  colour: z.string(),
  confidence: z.number(),
  faults: z.array(z.string()),
})
export type GateModel = z.infer<typeof gateModelSchema>

export const lampModelSchema = z.object({ observed: z.string() })
export type LampModel = z.infer<typeof lampModelSchema>

export const assistModelSchema = z.object({
  description: z.string(),
  boxes: z.array(boundingBoxSchema),
  recommendation: z.string(),
})
export type AssistModel = z.infer<typeof assistModelSchema>

export const parkingModelSchema = z.object({ decision: z.string(), confidence: z.number() })
export type ParkingModel = z.infer<typeof parkingModelSchema>

export const routeModelSchema = z.object({
  route: z.string(),
  confidence: z.number(),
  reason: z.string(),
})
export type RouteModel = z.infer<typeof routeModelSchema>

export const yardModelSchema = z.object({ moves: z.array(yardMoveSchema), reason: z.string() })
export type YardModel = z.infer<typeof yardModelSchema>
