// Shared context handed to every flow by the orchestrator. Flows only see interfaces: the
// DrivingSystem contract, the gate / evidence / parking ports, the MarshalAi client and the bus.

import type { MarshalAi } from '../../serverFns/client'
import type { EventBus, MarshalEventMap, MarshalNote } from '../eventBus'
import type { DecisionTag, MarshalState } from '../state'
import type {
  DrivingSystem,
  EvidencePort,
  GateCapturePort,
  GateView,
  MissionGoCmd,
  ParkingPort,
  SimCommand,
  YardMove,
} from '../types'

/** Optional truth hints for the offline AI (the scripted engine provides them). */
export interface TruthHintPort {
  /**
   * Per-view gate hints, e.g. 'colour=red'.
   * @param vin - Car at the gate.
   * @returns Hints by view.
   */
  gateHints?(vin: string): Partial<Record<GateView, string>> | undefined
  /**
   * Evidence hint, e.g. 'headlights=off'.
   * @param vin - Car on the test track.
   * @param camera - Evidence camera.
   * @returns Hint string.
   */
  evidenceHint?(vin: string, camera: 'front' | 'rear'): string | undefined
  /**
   * Assist hint, e.g. 'worker=1'.
   * @param case_id - Assist case.
   * @returns Hint string.
   */
  assistHint?(case_id: string): string | undefined
}

/** Why a mission was sent; decides which flow handles its `mission.done`. */
export type MissionPurpose = 'gate' | 'track' | 'report' | 'rework' | 'yard' | 'yard_move'

/** Orchestrator bookkeeping for the latest mission of a car. */
export type MissionRecord = {
  id: string
  purpose: MissionPurpose
  waypoints: string[]
  routine_steps?: number[]
  /** R-12 step numbers that failed during this mission. */
  failed: number[]
  /** Lamp evaluations still running for this mission. */
  pending: Promise<void>[]
}

/** One yard move in dispatch order. */
export type OrderedMove = YardMove & { waitsFor?: string }

/** Context passed to every flow. */
export type FlowContext = {
  ds: DrivingSystem
  gate: GateCapturePort
  evidence: EvidencePort
  parking: ParkingPort
  ai: MarshalAi
  bus: EventBus<MarshalEventMap>
  hints?: TruthHintPort
  /** Current state snapshot. */
  state(): MarshalState
  /** Applies a pure reducer and notifies subscribers. */
  update(reducer: (state: MarshalState) => MarshalState): void
  /** Logs a decision for a car. */
  decide(
    vin: string,
    tag: DecisionTag,
    text: string,
    opts?: { exception?: boolean; degraded?: boolean; invalidInput?: boolean },
  ): void
  /** Sends a command to the engine and mirrors it on the bus. */
  send(cmd: SimCommand): void
  /** Sends `mission.go` and records it as the car's current mission. */
  go(
    vin: string,
    waypoints: string[],
    purpose: MissionPurpose,
    extra?: Pick<MissionGoCmd, 'routine' | 'routine_steps' | 'max_speed_kmh'>,
  ): MissionRecord
  /** Current mission of a car. */
  mission(vin: string): MissionRecord | undefined
  /** Emits a dashboard note. */
  note(text: string, kind: MarshalNote['kind']): void
  /** Sim time in seconds. */
  now(): number
  /** Runs a flow in the background; errors are logged as PLANT exceptions for `vin`. */
  run(vin: string, task: Promise<void>): void
  /** Parking attempts per VIN (bounds parking -> assist -> parking retries). */
  parkingAttempts: Map<string, number>
  /** Assist cases raised by the parking flow. */
  parkingCases: Set<string>
  /** Yard re-plan dispatch queue. */
  yard: { queue: OrderedMove[]; active: OrderedMove | null }
}
