// A MarshalAi that never leaves the process: every method answers with the deterministic offline
// logic (degraded: true). Used by tests and the scripted demo; individual methods can be
// overridden to script Gemini answers.

import { createMarshalAi, type MarshalAi } from '../../serverFns/client'
import {
  offlineAssist,
  offlineGate,
  offlineLamp,
  offlineParking,
  offlineRoute,
  offlineYard,
} from '../../serverFns/logic'

/**
 * Creates an offline MarshalAi.
 * @param overrides - Methods to replace (e.g. a scripted `replanYardSlots`).
 * @returns MarshalAi backed by the offline logic.
 */
export function createOfflineMarshalAi(overrides: Partial<MarshalAi> = {}): MarshalAi {
  return createMarshalAi({
    evaluateGatePhotos: async (input) => offlineGate(input),
    evaluateLampEvidence: async (input) => offlineLamp(input),
    analyzeAssistClip: async (input) => offlineAssist(input),
    planParkingStep: async (input) => offlineParking(input),
    chooseRouteHazard: async (input) => offlineRoute(input),
    replanYardSlots: async (input) => offlineYard(input),
    ...overrides,
  })
}
