// Shift store (zustand) for the dashboard. Owns the Marshal orchestrator and its
// DrivingSystem backend, drives the backend clock with requestAnimationFrame (play, pause, next
// exception, restart, speed) and publishes orchestrator snapshots. The backend defaults to the
// ScriptedDrivingSystem and can be swapped with `setBackend` (e.g. the three.js engine).

import { create } from 'zustand'
import type { TruthHintPort } from '#/lib/marshal/flows/context'
import type { YardConstraints } from '#/lib/marshal/flows/yard'
import { clearHazard, type HazardPreset, type HazardSite, stageHazard } from '#/lib/marshal/hazards'
import { type CarSpec, createOrchestrator, type Orchestrator } from '#/lib/marshal/orchestrator'
import { createInitialState, type MarshalState, type Truck } from '#/lib/marshal/state'
import { type LampFault, ScriptedDrivingSystem } from '#/lib/marshal/testing/scriptedSystem'
import type {
  AssistOption,
  DrivingSystem,
  EvidencePort,
  GateCapturePort,
  ParkingPort,
} from '#/lib/marshal/types'
import { createMarshalAi, type MarshalAi } from '#/lib/serverFns/client'
import { offlineAssist, offlineGate, offlineLamp } from '#/lib/serverFns/logic'
import { autoFocusKeys, chooseFocus, type SimFocus } from '#/lib/simview/focus'

/**
 * Everything the orchestrator needs from an engine. `tick` and `time` are optional: a
 * self-driven engine (three.js render loop) omits `tick` and the store only reads its clock.
 */
export type ShiftBackend = DrivingSystem &
  GateCapturePort &
  EvidencePort &
  ParkingPort &
  TruthHintPort & {
    /** Advances the engine by `dt` sim seconds. */
    tick?: (dt: number) => void
    /** Sim time in seconds. */
    readonly time?: number
    /**
     * True when gate, evidence and assist frames are placeholders rather than JPEGs (scripted
     * engine). Image checks then stay offline instead of sending them to Gemini.
     */
    readonly placeholderFrames?: boolean
  }

/** A scripted blocker: props appear next to a car, which stops and asks for help. */
export type ShiftBlocker = {
  vin: string
  /** Sim second the blocker appears. */
  at: number
  reason: string
  options: AssistOption[]
  /** Also place a worker next to the box (Gemini then recommends waiting). */
  withWorker?: boolean
}

/** Scene A: a hazard staged at a site; the route choice sees its description until cleared. */
export type ShiftHazard = {
  /** Sim second the hazard appears. */
  at: number
  /** Sim second the hazard is cleared (kept for the whole shift when absent). */
  clearAt?: number
  site: HazardSite
  preset: HazardPreset
}

/** Scene D: a truck runs late and the yard is re-planned. */
export type ShiftLateTruck = {
  /** Sim second the truck is reported late. */
  at: number
  /** Dock truck id; its state becomes 'late'. */
  truck: string
  constraints: YardConstraints
}

/** What runs on a shift. */
export type ShiftScenario = {
  cars: CarSpec[]
  trucks?: Truck[]
  reservedSlots?: string[]
  /** Lamp faults set right after the cars spawn. */
  faults?: Record<string, LampFault[]>
  blockers?: ShiftBlocker[]
  hazards?: ShiftHazard[]
  lateTrucks?: ShiftLateTruck[]
}

/**
 * The demo shift: a clean car parked in A7 right behind a car already in A8 (Scene E; slots
 * A1..A8 are the sim's yard.Y1..Y8, so this is TEAM_PLAN's "Y7 behind Y8"), a blocked lane, a
 * wrong colour, a dark headlight, a forklift at the finishing corner (Scene A) and a late truck
 * that triggers a yard re-plan (Scene D).
 */
export const DEFAULT_SCENARIO: ShiftScenario = {
  cars: [
    { vin: 'P001', colour: 'blue', targetSlot: 'A7' },
    { vin: 'P002', colour: 'white' },
    { vin: 'P003', colour: 'red', expectedColour: 'white' },
    { vin: 'P004', colour: 'silver' },
    { vin: 'P005', colour: 'black' },
    { vin: 'P090', colour: 'green', parkedAt: 'A5' },
    { vin: 'P091', colour: 'white', parkedAt: 'A8' },
  ],
  trucks: [
    { id: 'T-1', state: 'loading', slots: ['A5'] },
    { id: 'T-2', state: 'truck', slots: [] },
    { id: 'T-3', state: 'truck', slots: [] },
  ],
  faults: { P004: ['headlights'] },
  blockers: [
    {
      vin: 'P002',
      at: 20,
      reason: 'Box and worker blocking the lane',
      options: ['wait_for_removal', 'pass_left_3kmh', 'reroute_lane_c'],
      withWorker: true,
    },
  ],
  hazards: [{ at: 60, clearAt: 180, site: 'finishing_corner', preset: 'forklift_unloading' }],
  lateTrucks: [
    {
      at: 150,
      truck: 'T-1',
      constraints: {
        priorityVins: ['P090'],
        reason: 'Late truck T-1: P090 moves up to the front row',
      },
    },
  ],
}

/** Sim seconds the yard crew needs to clear a blocker for a car waiting for removal. */
export const CREW_CLEAR_S = 6

/** After a pass / reroute the crew clears the blocker once the car is this far from it. */
const CREW_PASSED_M = 12

/** ...or after this many sim seconds at the latest. */
const CREW_GIVE_UP_S = 60

/** Largest single engine step, in sim seconds. */
export const MAX_STEP_S = 0.5

/** Speed used by "Next exception" while fast-forwarding. */
export const SKIP_SPEED = 32

/** Sim seconds "Next exception" fast-forwards at most. */
export const SKIP_LIMIT_S = 900

/** Shift store state and actions. */
export type ShiftStore = {
  /** Latest orchestrator snapshot. */
  shift: MarshalState
  /** Cars planned for the shift. */
  totalCars: number
  playing: boolean
  /** True while "Next exception" fast-forwards. */
  skipping: boolean
  /** True when the shift stopped because a case needs the supervisor. */
  haltedForYou: boolean
  /** Sim seconds per real second. */
  speed: number
  /** Selected VIN (car detail and 3D focus). */
  selected: string | null
  /** Case picked with "Show in 3D" (focused while it stays open). */
  focusedCase: string | null
  /** Automatic 3D focus targets dismissed with "Overview" (see `autoFocusKey`). */
  dismissedFocus: string[]
  /** Creates the orchestrator on first use (no-op afterwards). */
  ensureStarted: () => void
  play: () => void
  pause: () => void
  togglePlay: () => void
  /** Fast-forwards until the next exception (or the end of the shift). */
  nextException: () => void
  /** Rebuilds the backend and orchestrator and starts the scenario again. */
  restart: () => void
  setSpeed: (speed: number) => void
  setPauseWhenNeeded: (on: boolean) => void
  /**
   * Decides an assist case as the supervisor.
   * @returns True when the orchestrator accepted the choice.
   */
  choose: (case_id: string, option: AssistOption) => boolean
  /** Selects a car (null clears it); also drops a picked case. */
  select: (vin: string | null) => void
  /** Focuses the 3D view (and the car detail) on an assist case's car. */
  focusCase: (case_id: string) => void
  /** Returns the 3D view to the plant overview until a new decision comes up. */
  showOverview: () => void
  /** Advances the backend by `seconds` (stops early when the shift pauses for you). */
  advance: (seconds: number) => void
  /** Swaps the engine (instance or factory used on every restart) and restarts. */
  setBackend: (backend: ShiftBackend | (() => ShiftBackend)) => void
  /** Replaces the AI client (null restores the default), scenario and/or backend; restarts. */
  configure: (opts: {
    ai?: MarshalAi | null
    scenario?: ShiftScenario
    backend?: ShiftBackend | (() => ShiftBackend)
  }) => void
}

let backendSource: ShiftBackend | (() => ShiftBackend) = () => new ScriptedDrivingSystem()
let aiClient: MarshalAi | null = null
let scenario: ShiftScenario = DEFAULT_SCENARIO
let backend: ShiftBackend | null = null
let orchestrator: Orchestrator | null = null
let unsubscribe: (() => void) | null = null
let publishQueued = false
let frameId: ReturnType<typeof setTimeout> | number | null = null
let lastFrame: number | null = null
let skipBase = 0
let skipUntil = 0
let fired = new Set<string>()
/** Assist case id -> prop ids to clear once the case is decided. */
let blockerProps = new Map<string, string[]>()
/** Assist case id -> where its box stands. */
let blockerSpots = new Map<string, { x: number; y: number }>()
/** Assist case id -> sim second the case was first seen decided. */
let blockerDecided = new Map<string, number>()
/** Hazard index -> staged prop ids. */
let hazardProps = new Map<number, string[]>()

/**
 * The AI client used when none is configured: Gemini via the server functions, except that the
 * image checks (gate, lamp, assist) stay offline while the engine only has placeholder frames.
 * @param engine - The running engine.
 * @returns MarshalAi for the shift.
 */
export function defaultShiftAi(engine: Pick<ShiftBackend, 'placeholderFrames'>): MarshalAi {
  if (!engine.placeholderFrames) return createMarshalAi()
  return createMarshalAi({
    evaluateGatePhotos: async (input) => offlineGate(input),
    evaluateLampEvidence: async (input) => offlineLamp(input),
    analyzeAssistClip: async (input) => offlineAssist(input),
  })
}

/**
 * The running engine (for the 3D view slot); null before the shift starts.
 * @returns Current backend.
 */
export function getShiftBackend(): ShiftBackend | null {
  return backend
}

function exceptionCount(state: MarshalState): number {
  let count = 0
  for (const entry of state.feed) if (entry.exception) count++
  return count
}

function isDone(state: MarshalState): boolean {
  return state.order.length > 0 && state.progress >= 1
}

function backendTime(): number | undefined {
  const time = backend?.time
  return typeof time === 'number' && Number.isFinite(time) ? time : undefined
}

function publish(): void {
  if (publishQueued) return
  publishQueued = true
  queueMicrotask(() => {
    publishQueued = false
    if (orchestrator) useShiftStore.setState({ shift: orchestrator.getState() })
  })
}

function teardown(): void {
  stopLoop()
  unsubscribe?.()
  unsubscribe = null
  orchestrator?.dispose()
  orchestrator = null
  backend = null
}

function build(): void {
  teardown()
  const engine = typeof backendSource === 'function' ? backendSource() : backendSource
  backend = engine
  fired = new Set()
  blockerProps = new Map()
  blockerSpots = new Map()
  blockerDecided = new Map()
  hazardProps = new Map()
  const pauseWhenNeeded = useShiftStore.getState().shift.pauseWhenNeeded
  const hasClock = backendTime() !== undefined
  const orch = createOrchestrator({
    ds: engine,
    gate: engine,
    evidence: engine,
    parking: engine,
    hints: engine,
    ai: aiClient ?? defaultShiftAi(engine),
    ...(hasClock ? { clock: () => backendTime() ?? 0 } : {}),
    pauseWhenNeeded,
  })
  orchestrator = orch
  unsubscribe = orch.subscribe(publish)
  orch.start(scenario.cars, {
    trucks: (scenario.trucks ?? []).map((truck) => ({ ...truck, slots: [...truck.slots] })),
    reservedSlots: [...(scenario.reservedSlots ?? [])],
  })
  for (const [vin, faults] of Object.entries(scenario.faults ?? {})) {
    engine.setFault(vin, new Set(faults))
  }
  useShiftStore.setState({
    shift: orch.getState(),
    totalCars: scenario.cars.length,
    playing: false,
    skipping: false,
    haltedForYou: false,
    selected: null,
    focusedCase: null,
    dismissedFocus: [],
  })
}

function runBlockers(state: MarshalState): void {
  const engine = backend
  if (!engine) return
  const now = state.simTime
  for (const blocker of scenario.blockers ?? []) {
    const case_id = `AC-${blocker.vin}-1`
    const car = state.cars[blocker.vin]
    if (fired.has(case_id) || !car || now < blocker.at) continue
    if (car.stage === 'parked' || car.stage === 'shipped' || car.stage === 'waiting') continue
    fired.add(case_id)
    const at = engine.getPositionXY(blocker.vin)
    const yaw = (at.yaw * Math.PI) / 180
    const ahead = (metres: number, side = 0) => ({
      x: at.x + metres * Math.cos(yaw) - side * Math.sin(yaw),
      y: at.y + metres * Math.sin(yaw) + side * Math.cos(yaw),
    })
    const ids = [`blk-${blocker.vin}-box`]
    const box = ahead(6)
    engine.spawnProp(ids[0], 'box', box.x, box.y, at.yaw)
    if (blocker.withWorker) {
      const worker = ahead(7, 1)
      ids.push(`blk-${blocker.vin}-worker`)
      engine.spawnProp(ids[1], 'worker', worker.x, worker.y, at.yaw + 90)
    }
    blockerProps.set(case_id, ids)
    blockerSpots.set(case_id, box)
    engine.raiseAssist(blocker.vin, case_id, blocker.reason, [...blocker.options])
  }
  // The yard crew clears a blocker once the case is decided: after CREW_CLEAR_S when the car
  // waits for removal, or once the car has driven around it (pass left / reroute).
  for (const [case_id, ids] of blockerProps) {
    const item = state.cases[case_id]
    if (item?.status !== 'decided') continue
    const decidedAt = blockerDecided.get(case_id) ?? now
    blockerDecided.set(case_id, decidedAt)
    const spot = blockerSpots.get(case_id)
    const car = engine.getPositionXY(item.vin)
    const gone = car.x === 0 && car.y === 0 && car.yaw === 0
    const past = !spot || gone || Math.hypot(car.x - spot.x, car.y - spot.y) > CREW_PASSED_M
    const waited = now - decidedAt
    const cleared =
      item.chosen === 'wait_for_removal'
        ? waited >= CREW_CLEAR_S
        : (past && waited > 0) || waited >= CREW_GIVE_UP_S
    if (!cleared) continue
    for (const id of ids) engine.removeProp(id)
    blockerProps.delete(case_id)
  }
  runHazards(engine, now)
  runLateTrucks(now)
}

function runHazards(engine: ShiftBackend, now: number): void {
  const orch = orchestrator
  if (!orch) return
  for (const [index, hazard] of (scenario.hazards ?? []).entries()) {
    const key = `hazard-${index}`
    if (!fired.has(key) && now >= hazard.at) {
      fired.add(key)
      const staged = stageHazard(engine, hazard.site, hazard.preset)
      hazardProps.set(index, staged.ids)
      orch.setHazard(staged.description)
    }
    const ids = hazardProps.get(index)
    if (ids && hazard.clearAt !== undefined && now >= hazard.clearAt) {
      clearHazard(engine, ids)
      hazardProps.delete(index)
      orch.setHazard(null)
    }
  }
}

function runLateTrucks(now: number): void {
  const orch = orchestrator
  if (!orch) return
  for (const [index, late] of (scenario.lateTrucks ?? []).entries()) {
    const key = `late-${index}`
    if (fired.has(key) || now < late.at) continue
    fired.add(key)
    orch
      .applyYardReplan(late.constraints, { truck: { id: late.truck, state: 'late' } })
      .catch((error: unknown) => console.error('[shift] yard re-plan failed:', error))
  }
}

/**
 * Steps the engine; returns early when the shift pauses for the supervisor.
 * @param seconds - Sim seconds to advance.
 * @returns True when the shift is paused for you.
 */
function step(seconds: number): boolean {
  const orch = orchestrator
  const engine = backend
  if (!orch || !engine) return false
  runBlockers(orch.getState())
  if (orch.getState().paused) return true
  let left = seconds
  while (left > 1e-9) {
    const dt = Math.min(MAX_STEP_S, left)
    engine.tick?.(dt)
    left -= dt
    runBlockers(orch.getState())
    if (orch.getState().paused) return true
  }
  return false
}

const useRaf = typeof requestAnimationFrame === 'function'

function stopLoop(): void {
  if (frameId !== null) {
    if (useRaf) cancelAnimationFrame(frameId as number)
    else clearTimeout(frameId)
  }
  frameId = null
  lastFrame = null
}

function schedule(): void {
  if (frameId !== null) return
  frameId = useRaf ? requestAnimationFrame(frame) : setTimeout(() => frame(Date.now()), 16)
}

function frame(now: number): void {
  frameId = null
  const store = useShiftStore.getState()
  const orch = orchestrator
  if (!store.playing || !orch) {
    lastFrame = null
    return
  }
  const realDt = lastFrame === null ? 0 : Math.min(0.1, Math.max(0, (now - lastFrame) / 1000))
  lastFrame = now
  const halted = step(realDt * (store.skipping ? SKIP_SPEED : store.speed))
  const state = orch.getState()
  publish()
  if (halted) {
    useShiftStore.setState({ playing: false, skipping: false, haltedForYou: true })
    lastFrame = null
    return
  }
  const skipDone =
    store.skipping && (exceptionCount(state) > skipBase || state.simTime >= skipUntil)
  if (isDone(state) || skipDone) {
    useShiftStore.setState({ playing: false, skipping: false })
    lastFrame = null
    return
  }
  schedule()
}

/** The shift dashboard store. */
export const useShiftStore = create<ShiftStore>((set, get) => ({
  shift: createInitialState(true),
  totalCars: 0,
  playing: false,
  skipping: false,
  haltedForYou: false,
  speed: 4,
  selected: null,
  focusedCase: null,
  dismissedFocus: [],

  ensureStarted() {
    if (!orchestrator) build()
  },

  play() {
    get().ensureStarted()
    const state = orchestrator?.getState()
    if (state && isDone(state)) build()
    if (orchestrator?.getState().paused) {
      set({ haltedForYou: true })
      return
    }
    set({ playing: true, skipping: false, haltedForYou: false })
    schedule()
  },

  pause() {
    set({ playing: false, skipping: false })
    stopLoop()
  },

  togglePlay() {
    if (get().playing) get().pause()
    else get().play()
  },

  nextException() {
    get().ensureStarted()
    const state = orchestrator?.getState()
    if (!state || state.paused || isDone(state)) return
    skipBase = exceptionCount(state)
    skipUntil = state.simTime + SKIP_LIMIT_S
    set({ playing: true, skipping: true, haltedForYou: false })
    schedule()
  },

  restart() {
    build()
  },

  setSpeed(speed) {
    const value = Number.isFinite(speed) ? Math.max(0.25, Math.min(64, speed)) : 4
    set({ speed: value })
  },

  setPauseWhenNeeded(on) {
    get().ensureStarted()
    orchestrator?.setPauseWhenNeeded(on)
    if (orchestrator) set({ shift: orchestrator.getState() })
    if (!on && get().haltedForYou) get().play()
  },

  choose(case_id, option) {
    const orch = orchestrator
    if (!orch) return false
    const accepted = orch.chooseAssist(case_id, option, 'supervisor')
    if (!accepted) return false
    set({ shift: orch.getState() })
    if (get().haltedForYou && !orch.getState().paused) get().play()
    return true
  },

  select(vin) {
    set({ selected: vin, focusedCase: null })
  },

  focusCase(case_id) {
    const item = get().shift.cases[case_id]
    if (!item) return
    set({ selected: item.vin, focusedCase: case_id })
  },

  showOverview() {
    set({ selected: null, focusedCase: null, dismissedFocus: autoFocusKeys(get().shift) })
  },

  advance(seconds) {
    get().ensureStarted()
    if (step(Math.max(0, seconds))) set({ haltedForYou: true })
    publish()
  },

  setBackend(next) {
    backendSource = next
    build()
  },

  configure(opts) {
    if (opts.ai !== undefined) aiClient = opts.ai
    if (opts.scenario) scenario = opts.scenario
    if (opts.backend) backendSource = opts.backend
    build()
  },
}))

/**
 * The 3D view's focus for a store state (see `chooseFocus`).
 * @param store - Shift store state.
 * @returns Focused car, or null for the overview.
 */
export function shiftFocus(
  store: Pick<ShiftStore, 'shift' | 'selected' | 'focusedCase' | 'dismissedFocus'>,
): SimFocus | null {
  return chooseFocus(store.shift, {
    selected: store.selected,
    focusedCase: store.focusedCase,
    dismissed: store.dismissedFocus,
  })
}
