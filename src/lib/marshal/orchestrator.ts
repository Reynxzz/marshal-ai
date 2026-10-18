// Marshal orchestrator. Listens to the DrivingSystem, mirrors its events and every command on
// the bus, keeps the dashboard state and runs the flows (gate, lamp, route, parking, assist,
// yard). It only depends on interfaces: the contract, the ports, the MarshalAi client (Gemini
// via server functions) and the bus. No three.js, no React, no AI SDK, no fetch.

import type { MarshalAi } from '../serverFns/client'
import { type EventBus, type MarshalEventMap, bus as sharedBus } from './eventBus'
import { autoChooseOpen, chooseAssist, handleAssistRequest } from './flows/assist'
import type { FlowContext, MissionPurpose, MissionRecord, TruthHintPort } from './flows/context'
import { handleGateArrival } from './flows/gate'
import { handleReworkDone, handleTrackStep } from './flows/lamp'
import { runParking } from './flows/parking'
import { handleReportArrival, retryHeldAtReport } from './flows/route'
import {
  handleMoveDone,
  requestYardReplan,
  type YardConstraints,
  type YardReplanOutcome,
} from './flows/yard'
import {
  addCar,
  addDecision,
  colourToRgb,
  createInitialState,
  derive,
  type MarshalState,
  patchCar,
  setSlot,
  slotToPoint,
  type Truck,
} from './state'
import type {
  AssistOption,
  DrivingSystem,
  EvidencePort,
  GateCapturePort,
  MissionDoneEvent,
  ParkingPort,
  SimCommand,
  StatusEvent,
} from './types'

/** Default mission speed (TEAM_PLAN: max 10 km/h in the plant). */
export const MISSION_SPEED_KMH = 10

/** Queue spacing behind `line_end`. */
export const QUEUE_SPACING_M = 9

/** One car to put on the shift. */
export type CarSpec = {
  vin: string
  /** Actual paint colour. */
  colour: string
  /** Build-sheet colour (defaults to `colour`). */
  expectedColour?: string
  /** Pre-park the car in a yard slot (e.g. 'A8') instead of starting at the line end. */
  parkedAt?: string
  /** Yard slot to assign at the report point when it is free (e.g. 'A7' for Scene E). */
  targetSlot?: string
}

/** Options for {@link createOrchestrator}. */
export type OrchestratorOptions = {
  ds: DrivingSystem
  gate: GateCapturePort
  evidence: EvidencePort
  parking: ParkingPort
  ai: MarshalAi
  /** Event bus (defaults to the shared Marshal bus). */
  bus?: EventBus<MarshalEventMap>
  /** Offline truth hints (the scripted engine provides them). */
  hints?: TruthHintPort
  /** Sim clock in seconds (defaults to wall time since creation). */
  clock?: () => number
  /** Initial "Pause when needed" toggle (default true). */
  pauseWhenNeeded?: boolean
}

/** The orchestrator handle used by the dashboard. */
export type Orchestrator = {
  /**
   * Spawns cars and starts the shift.
   * @param cars - Cars in queue order.
   * @param opts - Dock trucks and yard slots reserved from the start.
   */
  start(cars: CarSpec[], opts?: { trucks?: Truck[]; reservedSlots?: string[] }): void
  /** @returns Current state snapshot. */
  getState(): MarshalState
  /**
   * Subscribes to state changes.
   * @param cb - Called with every new snapshot.
   * @returns Unsubscribe function.
   */
  subscribe(cb: (state: MarshalState) => void): () => void
  /**
   * Decides an open assist case.
   * @param case_id - Case id.
   * @param option - Offered option.
   * @param by - Who chose (default 'supervisor').
   * @returns True when accepted.
   */
  chooseAssist(case_id: string, option: AssistOption, by?: 'supervisor' | 'gemini'): boolean
  /**
   * Sets "Pause when needed"; switching it off applies Gemini's pending recommendations.
   * @param on - New value.
   */
  setPauseWhenNeeded(on: boolean): void
  /**
   * Sets (or clears) the hazard description used for the route choice.
   * @param description - Hazard text, or null.
   */
  setHazard(description: string | null): void
  /**
   * Requests a yard re-plan and dispatches the valid moves.
   * @param constraints - Re-plan constraints.
   * @param opts - Optional truck state change that triggered it (e.g. late truck).
   * @returns Dispatch order and rejected moves.
   */
  applyYardReplan(
    constraints: YardConstraints,
    opts?: { truck?: { id: string; state: Truck['state'] } },
  ): Promise<YardReplanOutcome>
  /**
   * A dock truck leaves: its loaded cars are shipped (despawned, slots freed) and its state
   * becomes 'left'. Cars held at the report point are then offered the freed slots.
   * @param id - Dock truck id.
   * @returns Shipped VINs.
   */
  shipTruck(id: string): string[]
  /** Stops reacting to engine events and drops subscribers. */
  dispose(): void
}

/**
 * Creates the Marshal orchestrator.
 * @param options - Engine, ports, AI client, bus and clock.
 * @returns Orchestrator handle.
 */
export function createOrchestrator(options: OrchestratorOptions): Orchestrator {
  const { ds } = options
  const events = options.bus ?? sharedBus
  const started = Date.now()
  const clock = options.clock ?? (() => (Date.now() - started) / 1000)
  const subscribers = new Set<(state: MarshalState) => void>()
  const missions = new Map<string, MissionRecord>()
  const missionCounts = new Map<string, number>()
  let state = createInitialState(options.pauseWhenNeeded ?? true)
  let disposed = false
  let releasing = false

  const update = (reducer: (current: MarshalState) => MarshalState) => {
    if (disposed) return
    const next = reducer(state)
    if (next === state) return
    state = next
    for (const cb of [...subscribers]) cb(state)
    // Any change may free a yard slot (move done, car shipped): offer it to held cars.
    if (!releasing) batch(() => retryHeldAtReport(ctx))
  }

  /** Runs `fn` without re-entering the held-car retry, then retries once. */
  const batch = (fn: () => void) => {
    if (releasing) {
      fn()
      return
    }
    releasing = true
    try {
      fn()
      retryHeldAtReport(ctx)
    } finally {
      releasing = false
    }
  }

  const send = (cmd: SimCommand) => {
    if (disposed) return
    ds.send(cmd)
    events.emit('marshal:command', cmd)
  }

  const ctx: FlowContext = {
    ds,
    gate: options.gate,
    evidence: options.evidence,
    parking: options.parking,
    ai: options.ai,
    bus: events,
    hints: options.hints,
    state: () => state,
    update,
    decide(vin, tag, text, opts = {}) {
      update((current) =>
        addDecision(current, vin, {
          at: clock(),
          tag,
          text,
          exception: opts.exception ?? false,
          ...(opts.degraded ? { degraded: true } : {}),
          ...(opts.invalidInput ? { invalidInput: true } : {}),
        }),
      )
    },
    send,
    go(vin, waypoints, purpose: MissionPurpose, extra = {}) {
      const n = (missionCounts.get(vin) ?? 0) + 1
      missionCounts.set(vin, n)
      const record: MissionRecord = {
        id: `M-${vin}-${n}`,
        purpose,
        waypoints: [...waypoints],
        ...(extra.routine_steps ? { routine_steps: [...extra.routine_steps] } : {}),
        failed: [],
        pending: [],
      }
      missions.set(vin, record)
      send({
        op: 'mission.go',
        mission_id: record.id,
        vin,
        waypoints: [...waypoints],
        max_speed_kmh: extra.max_speed_kmh ?? MISSION_SPEED_KMH,
        ...(extra.routine ? { routine: extra.routine } : {}),
        ...(extra.routine_steps ? { routine_steps: [...extra.routine_steps] } : {}),
      })
      return record
    },
    mission: (vin) => missions.get(vin),
    note(text, kind) {
      if (!disposed) events.emit('ui:note', { text, kind })
    },
    now: clock,
    run(vin, task) {
      task.catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error)
        console.error(`[orchestrator] flow failed for ${vin}:`, error)
        ctx.decide(vin, 'PLANT', `Flow error: ${message}`, { exception: true })
      })
    },
    parkingAttempts: new Map(),
    parkingCases: new Set(),
    yard: { queue: [], active: null },
  }

  const tickTime = () => {
    const now = clock()
    if (now !== state.simTime) update((current) => ({ ...current, simTime: now }))
  }

  const onStatus = (event: StatusEvent) => {
    if (disposed) return
    events.emit('sim:status', event)
    const car = state.cars[event.vin]
    if (car && car.location !== event.location) {
      update((current) => patchCar(current, event.vin, { location: event.location }))
    }
    tickTime()
  }

  const onMissionDone = (event: MissionDoneEvent) => {
    if (disposed) return
    events.emit('sim:mission.done', event)
    tickTime()
    const mission = missions.get(event.vin)
    if (!mission || mission.id !== event.mission_id) return
    update((current) => patchCar(current, event.vin, { location: event.location }))
    switch (mission.purpose) {
      case 'gate':
        ctx.run(event.vin, handleGateArrival(ctx, event.vin))
        break
      case 'report':
        ctx.run(event.vin, handleReportArrival(ctx, event.vin))
        break
      case 'rework':
        handleReworkDone(ctx, event.vin)
        break
      case 'yard':
        ctx.run(event.vin, runParking(ctx, event.vin))
        break
      case 'yard_move':
        handleMoveDone(ctx, event.vin)
        break
      case 'track':
        // The lamp flow advances the car on the final track.step.
        break
    }
  }

  ds.on('status', onStatus)
  ds.on('mission.done', onMissionDone)
  ds.on('track.step', (event) => {
    if (disposed) return
    events.emit('sim:track.step', event)
    tickTime()
    ctx.run(event.vin, handleTrackStep(ctx, event))
  })
  ds.on('assist.request', (event) => {
    if (disposed) return
    events.emit('sim:assist.request', event)
    tickTime()
    ctx.run(event.vin, handleAssistRequest(ctx, event))
  })

  return {
    start(cars, opts = {}) {
      let queued = 0
      update((current) => ({
        ...current,
        trucks: opts.trucks ? [...opts.trucks] : current.trucks,
        reserved: [...new Set([...current.reserved, ...(opts.reservedSlots ?? [])])],
      }))
      for (const spec of cars) {
        const expectedColour = spec.expectedColour ?? spec.colour
        const point = spec.parkedAt ? slotToPoint(spec.parkedAt) : undefined
        if (spec.parkedAt && point) {
          const slot = spec.parkedAt
          ds.spawn(spec.vin, { at: point, colour: colourToRgb(spec.colour) })
          update((current) =>
            setSlot(
              addCar(current, {
                vin: spec.vin,
                colour: spec.colour,
                expectedColour,
                stage: 'parked',
                location: point,
                slot,
                preParked: true,
                decisions: [],
              }),
              slot,
              spec.vin,
            ),
          )
          continue
        }
        ds.spawn(spec.vin, {
          at: 'line_end',
          colour: colourToRgb(spec.colour),
          behind_m: queued * QUEUE_SPACING_M,
        })
        queued++
        update((current) =>
          addCar(current, {
            vin: spec.vin,
            colour: spec.colour,
            expectedColour,
            stage: 'to_gate',
            location: 'line_end',
            ...(spec.targetSlot ? { targetSlot: spec.targetSlot } : {}),
            decisions: [],
          }),
        )
        ctx.go(spec.vin, ['buffer_zone_b', 'inspection_gate'], 'gate')
      }
    },
    getState: () => state,
    subscribe(cb) {
      subscribers.add(cb)
      return () => {
        subscribers.delete(cb)
      }
    },
    chooseAssist: (case_id, option, by = 'supervisor') => chooseAssist(ctx, case_id, option, by),
    setPauseWhenNeeded(on) {
      update((current) => derive({ ...current, pauseWhenNeeded: on }))
      if (!on) autoChooseOpen(ctx)
    },
    setHazard(description) {
      const hazard = description?.trim() ? description.trim() : null
      update((current) => ({ ...current, hazard }))
    },
    async applyYardReplan(constraints, opts = {}) {
      const truck = opts.truck
      if (truck) {
        update((current) => ({
          ...current,
          trucks: current.trucks.map((item) =>
            item.id === truck.id ? { ...item, state: truck.state } : item,
          ),
        }))
      }
      return requestYardReplan(ctx, constraints)
    },
    shipTruck(id) {
      const truck = state.trucks.find((item) => item.id === id)
      if (!truck || truck.state === 'left') return []
      const loaded = truck.slots.flatMap((slot) => {
        const vin = state.layout[slot]
        return vin ? [{ slot, vin }] : []
      })
      for (const { vin } of loaded) ds.despawn(vin)
      batch(() => {
        update((current) => {
          let next: MarshalState = {
            ...current,
            trucks: current.trucks.map((item) =>
              item.id === id ? { ...item, state: 'left' } : item,
            ),
          }
          for (const { slot, vin } of loaded) {
            next = patchCar(setSlot(next, slot, null), vin, { stage: 'shipped', slot: undefined })
          }
          return derive(next)
        })
        for (const { slot, vin } of loaded) {
          ctx.decide(vin, 'PLANT', `Shipped on ${id} from ${slot}`)
        }
        ctx.note(`PLANT · ${id} left with ${loaded.length} car(s)`, 'plant')
      })
      return loaded.map(({ vin }) => vin)
    },
    dispose() {
      disposed = true
      subscribers.clear()
    },
  }
}
