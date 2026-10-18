// Browser-safe Marshal AI client. Calls the six server functions over createServerFn RPC (lazy
// imports, same pattern as src/ai/client.ts) and falls back to the deterministic offline answer
// when a call throws, so every method always resolves. Never reads env, never imports SDKs.

import { z } from 'zod'
import {
  type AssistInput,
  assistInputSchema,
  type GateInput,
  gateInputSchema,
  type LampInput,
  lampInputSchema,
  type ParkingInput,
  parkingInputSchema,
  type RouteInput,
  routeInputSchema,
  type YardInput,
  yardInputSchema,
} from '#/lib/marshal/schemas'
import type {
  AssistAnalysis,
  GateResult,
  LampResult,
  ParkingStep,
  RouteChoice,
  YardReplan,
} from '#/lib/marshal/types'
import {
  offlineAssist,
  offlineGate,
  offlineLamp,
  offlineParking,
  offlineRoute,
  offlineYard,
} from './logic'

/** The Marshal AI surface the orchestrator and flows depend on. */
export type MarshalAi = {
  /** Gate check (M3.3). */
  evaluateGatePhotos: (input: GateInput) => Promise<GateResult>
  /** Lamp check (M5.5). */
  evaluateLampEvidence: (input: LampInput) => Promise<LampResult>
  /** Assist clip analysis (M6.6). */
  analyzeAssistClip: (input: AssistInput) => Promise<AssistAnalysis>
  /** Parking step (M7.5). */
  planParkingStep: (input: ParkingInput) => Promise<ParkingStep>
  /** Route choice (M9.1). */
  chooseRouteHazard: (input: RouteInput) => Promise<RouteChoice>
  /** Yard re-plan (M9.2). */
  replanYardSlots: (input: YardInput) => Promise<YardReplan>
}

const remote: MarshalAi = {
  async evaluateGatePhotos(input) {
    const { evaluateGatePhotos } = await import('./gate')
    return evaluateGatePhotos({ data: input })
  },
  async evaluateLampEvidence(input) {
    const { evaluateLampEvidence } = await import('./lamp')
    return evaluateLampEvidence({ data: input })
  },
  async analyzeAssistClip(input) {
    const { analyzeAssistClip } = await import('./assist')
    return analyzeAssistClip({ data: input })
  },
  async planParkingStep(input) {
    const { planParkingStep } = await import('./parking')
    return planParkingStep({ data: input })
  },
  async chooseRouteHazard(input) {
    const { chooseRouteHazard } = await import('./route')
    return chooseRouteHazard({ data: input })
  },
  async replanYardSlots(input) {
    const { replanYardSlots } = await import('./yard')
    return replanYardSlots({ data: input })
  },
}

function isValidationError(error: unknown): boolean {
  return error instanceof z.ZodError || (error instanceof Error && error.name === 'ZodError')
}

function guard<I, O extends { degraded: boolean; invalidInput?: boolean }>(
  name: string,
  schema: z.ZodType<I>,
  call: (input: I) => Promise<O>,
  offline: (input: I) => O,
): (input: I) => Promise<O> {
  const rejected = (input: I, why: string): O => {
    console.warn(`[marshalAi:${name}] input rejected by schema, answering offline:`, why)
    return { ...offline(input), degraded: true, invalidInput: true }
  }
  return async (input) => {
    // Checked here too, so an over-limit request is reported as such instead of as "AI down".
    const parsed = schema.safeParse(input)
    if (!parsed.success) return rejected(input, parsed.error.message)
    try {
      return await call(input)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      if (isValidationError(error)) return rejected(input, message)
      console.warn(`[marshalAi:${name}] falling back to offline:`, message)
      return offline(input)
    }
  }
}

/**
 * Creates the Marshal AI client.
 * @param send - Optional per-method implementations (tests inject these instead of RPC).
 * @returns A {@link MarshalAi} whose methods never reject: on any error they return the
 * deterministic offline answer (`degraded: true`). Inputs that fail the server function schema
 * are not sent; they are answered offline with `invalidInput: true`.
 */
export function createMarshalAi(send: Partial<MarshalAi> = {}): MarshalAi {
  return {
    evaluateGatePhotos: guard(
      'evaluateGatePhotos',
      gateInputSchema,
      send.evaluateGatePhotos ?? remote.evaluateGatePhotos,
      offlineGate,
    ),
    evaluateLampEvidence: guard(
      'evaluateLampEvidence',
      lampInputSchema,
      send.evaluateLampEvidence ?? remote.evaluateLampEvidence,
      offlineLamp,
    ),
    analyzeAssistClip: guard(
      'analyzeAssistClip',
      assistInputSchema,
      send.analyzeAssistClip ?? remote.analyzeAssistClip,
      offlineAssist,
    ),
    planParkingStep: guard(
      'planParkingStep',
      parkingInputSchema,
      send.planParkingStep ?? remote.planParkingStep,
      (input) => offlineParking(input),
    ),
    chooseRouteHazard: guard(
      'chooseRouteHazard',
      routeInputSchema,
      send.chooseRouteHazard ?? remote.chooseRouteHazard,
      offlineRoute,
    ),
    replanYardSlots: guard(
      'replanYardSlots',
      yardInputSchema,
      send.replanYardSlots ?? remote.replanYardSlots,
      offlineYard,
    ),
  }
}
