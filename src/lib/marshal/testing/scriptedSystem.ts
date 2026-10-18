// Scripted, deterministic DrivingSystem for tests and for the dashboard demo until the three.js
// engine lands. No timers: time only moves when `tick(dt)` is called. Cars drive in straight
// lines between named points at the mission speed, R-12 runs as a fixed sequence of steps with
// healthy telemetry, and frames are tiny placeholder base64 strings. Faults only change the
// truth hints read by the offline AI (telemetry stays ON, per TEAM_PLAN M5.3).
//
// Traffic (TEAM_PLAN M4.1): cars never drive into each other. Each tick a car checks its path
// ahead and stops STANDING_GAP_M short of any car in the way (slowing proportionally when
// closer than the headway distance), so cars queue at the gate, the test-track pad, the report
// point and the yard entry, and yield when joining a lane. Queuing is silent (no assist
// request). The rework bay has side-by-side stands on one-way bay lanes, yard slots are left
// and entered through the yard lane, and a car stuck behind a car with no mission at its
// destination arrives at its queue spot. Waiting cycles (head-on, crossings) are broken by the
// latest-spawned car pulling aside.

import { isPointName, POINTS, YARD_SLOTS } from '../points'
import { rgbToColourName } from '../state'
import type {
  AssistChooseCmd,
  AssistOption,
  AssistRequestEvent,
  ControlStep,
  DrivingSystem,
  EvidencePort,
  GateCapturePort,
  GateFrames,
  GateView,
  MissionDoneEvent,
  MissionGoCmd,
  ObstacleBox,
  ParkingPort,
  ParkingSlot,
  Pose,
  SimCommand,
  SimEvent,
  StatusEvent,
  TrackStepEvent,
} from '../types'
import { CAR_LENGTH_M, CAR_WIDTH_M, carRect, headingDiff, type Rect, rectsOverlap } from './overlap'

/** Lamp faults the simulator supports. */
export type LampFault = 'headlights' | 'brake_lights' | 'indicators'

/** Prop kinds accepted by `spawnProp`. */
export type PropKind = 'box' | 'worker' | 'cone' | 'barrel' | 'truck'

/** A spawned prop. */
export type ScriptedProp = { id: string; kind: PropKind; x: number; y: number; yaw: number }

/** One R-12 step as emitted on `track.step`. */
export type RoutineStep = { step: number; name: string; telemetry: Record<string, unknown> }

/** Routine R-12 (TEAM_PLAN M5.2) with the always-healthy telemetry. */
export const R12_STEPS: readonly RoutineStep[] = [
  { step: 1, name: 'headlights', telemetry: { headlights: 'ON' } },
  {
    step: 2,
    name: 'indicators',
    telemetry: { indicator_left: 'BLINK', indicator_right: 'BLINK' },
  },
  { step: 3, name: 'brake_lights', telemetry: { brake_signal: true, brake_lamps: 'ON' } },
  { step: 4, name: 'reverse', telemetry: { gear: 'R', distance_m: 3.02, lateral_m: 0.05 } },
  { step: 5, name: 'brake_test', telemetry: { start_speed_kmh: 20, stopping_distance_m: 3.9 } },
  { step: 6, name: 'full_lock', telemetry: { radius_left_m: 5.4, radius_right_m: 5.5 } },
]

/** Options for {@link ScriptedDrivingSystem}. */
export type ScriptedOptions = {
  /** Sim seconds per R-12 step (default 2; the real routine takes minutes). */
  stepSeconds?: number
  /** Distance within which props block a car waiting for removal (default 15m). */
  clearRadius_m?: number
}

/**
 * One node of a mission's driven path. Named waypoints may expand into several nodes (pulling
 * out of / into a yard slot goes through the yard lane).
 */
type PathNode = {
  x: number
  y: number
  /** Waypoint name reached at this node (null for an intermediate lane node). */
  name: string | null
  /** Fixed heading while driving into this node (crab / reverse in the yard); null = travel. */
  yaw: number | null
  /** Clear gap kept while driving into this node (default {@link STANDING_GAP_M}). */
  gap?: number
  /** Speed limit while driving into this node (default: mission speed). */
  speed_mps?: number
  /** A side step (pull aside, pass an obstruction): skipped once the car is already past it. */
  transient?: boolean
  /** Props a passing detour goes around: skipped once they are all cleared. */
  around?: string[]
}

type ActiveMission = {
  cmd: MissionGoCmd
  /** Expanded path; `index` is the next node to reach. */
  path: PathNode[]
  index: number
  speed_mps: number
  total_m: number
  travelled_m: number
  /** Routine steps to run once the last waypoint is reached; null until it starts. */
  steps: RoutineStep[] | null
  stepIndex: number
  stepElapsed: number
}

type ScriptedCar = {
  vin: string
  rgb: [number, number, number]
  x: number
  y: number
  yaw: number
  location: string
  speed_kmh: number
  mission: ActiveMission | null
  held: boolean
  holdLeft: number | null
  stoppedForAssist: boolean
  /** Case the car waits on with `wait_for_removal`. */
  waitingCase: string | null
  waited_s: number
  faults: Set<LampFault>
  /** Spawn order (deadlock tie-break: the later car pulls aside). */
  seq: number
  /** Car this one is queued behind (null when free to move). */
  blockedBy: string | null
  /** Sim seconds spent queued without moving. */
  queued_s: number
  /** Sim seconds spent queued behind a car with no mission (it may never move). */
  idleQueue_s: number
  /** Pulled aside at (x, y) to let `vin` pass; waits there until it has gone by. */
  yieldTo: { vin: string; x: number; y: number } | null
}

type AssistRecord = { vin: string; options: AssistOption[]; frames: string[] }

type Listener = (event: any) => void

const DEG = Math.PI / 180
const WHEELBASE_M = 2.85
const MAX_STEER = 30 * DEG
const OBSTACLE_RANGE_M = 15
/** Clear gap kept in front of a car to the car ahead when standing (TEAM_PLAN M4.1). */
export const STANDING_GAP_M = 6
/**
 * Centre-to-centre distance a queued car stands behind the car ahead: the standing gap plus one
 * car length (kept for queue.test.ts and callers of the earlier centre-to-centre rule).
 */
export const QUEUE_GAP_M = STANDING_GAP_M + CAR_LENGTH_M
/** Moving cars slow down proportionally when the clear road ahead is under speed x this. */
export const HEADWAY_S = 1
/** Clearance kept on every side of a car's footprint. */
const CLEARANCE_M = 0.25
/** Path sampling step for the clearance check. */
const SAMPLE_M = 0.25
/**
 * A car queued this long behind a car with no mission (held at the report point or yard
 * entry) counts as arrived at its own queue position when it is close to its destination.
 */
export const IDLE_QUEUE_S = 10
/** Queue positions further than this (path metres) from the destination keep waiting. */
const QUEUE_ARRIVAL_M = 60
/** Cars that wait for each other this long (head-on, crossing) are untangled. */
const DEADLOCK_S = 1
/** How far a car pulls aside to let an oncoming car pass. */
const PULL_ASIDE_M = 3.2
/** Gap kept while pulling aside (it moves sideways, not into traffic). */
const PULL_ASIDE_GAP_M = 0.5
/** A car that pulled aside waits until the car it let pass is this far away. */
const YIELD_CLEAR_M = 12
/** Slowest approach speed, so a car closing up actually reaches its stopping point. */
const CRAWL_MPS = 0.5
/** The yard lane runs north through `yard.entry`; slots are pulled into from it. */
const YARD_LANE_X = POINTS['yard.entry'].x
const YARD_SLOT_NAMES = new Set<string>(YARD_SLOTS)
const REWORK = 'rework.electrical'
/** Side-by-side stands in the rework bay; stand 0 is the `rework.electrical` point. */
export const REWORK_STANDS = 8
/** Stand spacing, metres east of `rework.electrical`. */
const STAND_SPACING_M = 3.5
/** Bay lane (behind the stands) and exit lane (in front of them) distance from the stands. */
const STAND_ENTRY_M = 7
/** The bay lanes start / end this far west of stand 0. */
const BAY_LANE_LEAD_M = 6
/** 'pass_left_3kmh': lateral offset and speed while passing an obstruction. */
const PASS_OFFSET_M = 3
const PASS_SPEED_MPS = 3 / 3.6
/** 'reroute_lane_c' around an obstruction that is not before the finishing corner. */
const REROUTE_OFFSET_M = 4
/** Lead-in before a point that is approached from behind (TEAM_PLAN pull-over run, M4.2). */
const APPROACH_M = 12
const GATE_VIEWS: readonly GateView[] = ['front', 'rear', 'left', 'right']
const PROP_SIZE: Record<PropKind, { length: number; width: number; moving: boolean }> = {
  box: { length: 1.2, width: 1.2, moving: false },
  worker: { length: 0.6, width: 0.6, moving: true },
  cone: { length: 0.4, width: 0.4, moving: false },
  barrel: { length: 0.6, width: 0.6, moving: false },
  truck: { length: 8, width: 2.5, moving: false },
}

/** A straight piece of a car's remaining path (`from` / `to` are path metres from the car). */
type Leg = {
  ax: number
  ay: number
  bx: number
  by: number
  from: number
  to: number
  length: number
  /** Direction of travel, degrees. */
  heading: number
  /** Car heading while on the leg (differs from `heading` when crabbing or reversing). */
  yaw: number
  gap: number
}

/**
 * The clear road a car keeps in front of it: a `gap`-long box just past its leading edge in
 * the direction of travel, as wide as the car (seen along that direction) plus clearance.
 */
function gapBox(pose: Pose, heading: number, gap: number): Rect {
  const off = (pose.yaw - heading) * DEG
  const lead = (Math.abs(Math.cos(off)) * CAR_LENGTH_M + Math.abs(Math.sin(off)) * CAR_WIDTH_M) / 2
  const width = Math.abs(Math.sin(off)) * CAR_LENGTH_M + Math.abs(Math.cos(off)) * CAR_WIDTH_M
  const ahead = lead + gap / 2
  return {
    x: pose.x + ahead * Math.cos(heading * DEG),
    y: pose.y + ahead * Math.sin(heading * DEG),
    yaw: heading,
    length: gap,
    width: width + 2 * CLEARANCE_M,
  }
}

/** A rework stand and the one-way bay lanes around it. */
type ReworkStand = {
  stand: Pose
  /** Bay-lane point behind the stand (entered forwards from here). */
  entry: { x: number; y: number }
  /** Exit-lane point in front of the stand (left forwards to here). */
  exit: { x: number; y: number }
}

/**
 * Rework stand `k` (stand 0 is `rework.electrical`). The bay is one-way: cars join the bay lane
 * at its west end, drive east to their stand's entry, pull forwards into the stand, leave
 * forwards onto the exit lane and drive west out of the bay.
 * @param k - Stand index.
 * @returns Stand pose, entry and exit points.
 */
function reworkStand(k: number): ReworkStand {
  const bay = POINTS[REWORK]
  const c = Math.cos(bay.yaw * DEG)
  const sn = Math.sin(bay.yaw * DEG)
  const stand = { x: bay.x + k * STAND_SPACING_M, y: bay.y, yaw: bay.yaw }
  const entry = { x: stand.x - STAND_ENTRY_M * c, y: stand.y - STAND_ENTRY_M * sn }
  const exit = { x: stand.x + STAND_ENTRY_M * c, y: stand.y + STAND_ENTRY_M * sn }
  return { stand, entry, exit }
}

const SHOULDER_SPOTS: { x: number; y: number }[] = [
  ...YARD_SLOTS.map((slot) => POINTS[slot]),
  ...Array.from({ length: REWORK_STANDS }, (_, k) => reworkStand(k).stand),
]

/** True for a car standing in a yard slot or a rework stand. */
function onShoulder(car: { x: number; y: number }): boolean {
  return SHOULDER_SPOTS.some((spot) => Math.hypot(spot.x - car.x, spot.y - car.y) < 1)
}

/** West ends of the bay lane (in) and exit lane (out). */
function bayEnds(): { laneIn: { x: number; y: number }; laneOut: { x: number; y: number } } {
  const { entry, exit } = reworkStand(0)
  return {
    laneIn: { x: entry.x - BAY_LANE_LEAD_M, y: entry.y },
    laneOut: { x: exit.x - BAY_LANE_LEAD_M, y: exit.y },
  }
}

/**
 * Expands a mission's waypoints into the driven path:
 * - yard slots are left and entered through the yard lane (pull out sideways, drive or reverse
 *   along the lane, pull in), so slot-to-slot moves never cut through the parked cars;
 * - rework stands are reached through the one-way bay lanes (see {@link reworkStand});
 * - a point approached from behind (more than 90 degrees off its heading, e.g. the test track
 *   from the rework bay) is joined {@link APPROACH_M} before it, along its heading, so the car
 *   enters it the way the lane traffic does.
 * @param car - Car pose and location.
 * @param waypoints - Named waypoints.
 * @param stand - Rework stand for a `rework.electrical` waypoint.
 * @returns Path nodes.
 */
function buildPath(
  car: { x: number; y: number; yaw: number; location: string },
  waypoints: string[],
  stand: number,
): PathNode[] {
  const path: PathNode[] = []
  let x = car.x
  let y = car.y
  const push = (node: PathNode) => {
    path.push(node)
    x = node.x
    y = node.y
  }
  if (YARD_SLOT_NAMES.has(car.location) && Math.abs(car.x - YARD_LANE_X) > 0.5) {
    push({ x: YARD_LANE_X, y: car.y, name: null, yaw: car.yaw })
  }
  if (car.location === REWORK) {
    const { laneIn, laneOut } = bayEnds()
    const at = Array.from({ length: REWORK_STANDS }, (_, k) => reworkStand(k)).find(
      ({ stand: spot }) => Math.hypot(car.x - spot.x, car.y - spot.y) <= 0.5,
    )
    if (at) push({ ...at.exit, name: null, yaw: null })
    else {
      // Stopped short of the stands (bay full): go round them, keeping the bay one-way.
      const last = reworkStand(REWORK_STANDS - 1)
      if (Math.abs(car.y - laneIn.y) > 0.5 || car.x < laneIn.x)
        push({ ...laneIn, name: null, yaw: null })
      push({ x: last.entry.x + STAND_SPACING_M, y: last.entry.y, name: null, yaw: null })
      push({ x: last.exit.x + STAND_SPACING_M, y: last.exit.y, name: null, yaw: null })
    }
    push({ ...laneOut, name: null, yaw: null })
  }
  for (const name of waypoints) {
    const to = point(name)
    if (YARD_SLOT_NAMES.has(name)) {
      const onLane = Math.abs(x - YARD_LANE_X) <= 0.5
      push({ x: YARD_LANE_X, y: to.y, name: null, yaw: onLane ? to.yaw : null })
      push({ x: to.x, y: to.y, name, yaw: to.yaw })
    } else if (name === REWORK) {
      const { stand: at, entry } = reworkStand(stand)
      const { laneIn } = bayEnds()
      if (Math.abs(y - laneIn.y) > 0.5 || x < laneIn.x) push({ ...laneIn, name: null, yaw: null })
      push({ ...entry, name: null, yaw: null })
      push({ x: at.x, y: at.y, name, yaw: at.yaw })
    } else {
      const heading = Math.atan2(to.y - y, to.x - x) / DEG
      const far = Math.hypot(to.x - x, to.y - y) > 2 * APPROACH_M
      if (far && headingDiff(heading, to.yaw) > 90) {
        const back = to.yaw * DEG
        const approach = {
          x: to.x - APPROACH_M * Math.cos(back),
          y: to.y - APPROACH_M * Math.sin(back),
        }
        push({ ...approach, name: null, yaw: null })
      }
      push({ x: to.x, y: to.y, name, yaw: null })
    }
  }
  return path
}

/**
 * A placeholder evidence frame (base64 of a short label, not a real JPEG).
 * @param label - Frame label, e.g. 'VIN-1:front:0'.
 * @returns base64 string.
 */
export function placeholderFrame(label: string): string {
  return btoa(`frame:${label}`)
}

function point(name: string): Pose {
  if (!isPointName(name)) throw new Error(`Unknown map point: ${name}`)
  return POINTS[name]
}

/**
 * Deterministic stand-in for the three.js engine. Implements the DrivingSystem contract and the
 * gate, evidence and parking ports, plus truth hints for the offline AI.
 */
export class ScriptedDrivingSystem
  implements DrivingSystem, GateCapturePort, EvidencePort, ParkingPort
{
  /** Frames are text placeholders, not JPEGs: image checks must not be sent to Gemini. */
  readonly placeholderFrames = true
  readonly passes: Record<string, number> = {}
  readonly assistOutcomes: Record<string, unknown> = {}
  readonly parkingLog: Record<string, unknown[]> = {}
  /** Every command received, oldest first. */
  readonly commands: SimCommand[] = []
  /** Artifacts saved with `storeArtifact`. */
  readonly artifacts = new Map<string, Blob>()

  private readonly listeners = new Map<string, Set<Listener>>()
  private readonly cars = new Map<string, ScriptedCar>()
  private readonly props = new Map<string, ScriptedProp>()
  private readonly assists = new Map<string, AssistRecord>()
  private readonly stepSeconds: number
  private readonly clearRadius: number
  private clock = 0
  private spawned = 0

  /**
   * @param options - Routine step length and clear radius.
   */
  constructor(options: ScriptedOptions = {}) {
    this.stepSeconds = options.stepSeconds ?? 2
    this.clearRadius = options.clearRadius_m ?? 15
  }

  /** Sim time in seconds. */
  get time(): number {
    return this.clock
  }

  // --- Commands -------------------------------------------------------------------------------

  /**
   * Executes a command.
   * @param cmd - Orchestrator command.
   */
  send(cmd: SimCommand): void {
    this.commands.push(cmd)
    if (cmd.op === 'assist.choose') {
      this.choose(cmd)
      return
    }
    const car = this.cars.get(cmd.vin)
    if (!car) return
    if (cmd.op === 'mission.go') this.startMission(car, cmd)
    else if (cmd.op === 'mission.hold') {
      car.held = true
      car.holdLeft = cmd.duration_s ?? null
    } else {
      car.held = false
      car.holdLeft = null
    }
  }

  on(event: 'status', cb: (e: StatusEvent) => void): void
  on(event: 'mission.done', cb: (e: MissionDoneEvent) => void): void
  on(event: 'track.step', cb: (e: TrackStepEvent) => void): void
  on(event: 'assist.request', cb: (e: AssistRequestEvent) => void): void
  /**
   * Subscribes to simulator events.
   * @param event - Event name.
   * @param cb - Listener.
   */
  on(event: SimEvent['event'], cb: Listener): void {
    let set = this.listeners.get(event)
    if (!set) {
      set = new Set()
      this.listeners.set(event, set)
    }
    set.add(cb)
  }

  // --- Direct engine calls --------------------------------------------------------------------

  /**
   * Spawns a car at a named point, optionally `behind_m` metres behind it.
   * @param vin - Car VIN.
   * @param opts - Spawn point, RGB colour and queue offset.
   */
  spawn(
    vin: string,
    opts: { at: string; colour: [number, number, number]; behind_m?: number },
  ): void {
    const at = point(opts.at)
    const back = opts.behind_m ?? 0
    this.cars.set(vin, {
      vin,
      rgb: opts.colour,
      x: at.x - back * Math.cos(at.yaw * DEG),
      y: at.y - back * Math.sin(at.yaw * DEG),
      yaw: at.yaw,
      location: opts.at,
      speed_kmh: 0,
      mission: null,
      held: false,
      holdLeft: null,
      stoppedForAssist: false,
      waitingCase: null,
      waited_s: 0,
      faults: new Set(),
      seq: this.spawned++,
      blockedBy: null,
      queued_s: 0,
      idleQueue_s: 0,
      yieldTo: null,
    })
  }

  /**
   * Removes a car.
   * @param vin - Car VIN.
   */
  despawn(vin: string): void {
    this.cars.delete(vin)
  }

  /**
   * Sets lamp faults: the lamps stop rendering but telemetry still reports ON.
   * @param vin - Car VIN.
   * @param faults - Faulty lamps.
   */
  setFault(vin: string, faults: Set<LampFault>): void {
    const car = this.cars.get(vin)
    if (car) car.faults = new Set(faults)
  }

  /**
   * Clears the listed faults.
   * @param vin - Car VIN.
   * @param defects - Fault names to clear.
   */
  repair(vin: string, defects: string[]): void {
    const car = this.cars.get(vin)
    if (!car) return
    for (const defect of defects) car.faults.delete(defect as LampFault)
  }

  /**
   * Current car pose.
   * @param vin - Car VIN.
   * @returns Pose in the map frame (zeros for unknown cars).
   */
  getPositionXY(vin: string): Pose {
    const car = this.cars.get(vin)
    return car ? { x: car.x, y: car.y, yaw: car.yaw } : { x: 0, y: 0, yaw: 0 }
  }

  /**
   * Named point transform.
   * @param name - Named point.
   * @returns Point pose; throws for unknown points.
   */
  getTransform(name: string): Pose {
    return { ...point(name) }
  }

  /**
   * Stored clip for an assist case.
   * @param case_id - Case id.
   * @returns base64 frames ([] when unknown).
   */
  getAssistMedia(case_id: string): string[] {
    return [...(this.assists.get(case_id)?.frames ?? [])]
  }

  /**
   * Stops the car and emits `assist.request` with an 8-frame placeholder clip.
   * @param vin - Car VIN.
   * @param case_id - Case id, e.g. 'AC-VIN-1-1'.
   * @param reason - Obstacle description.
   * @param options - Offered options.
   */
  raiseAssist(vin: string, case_id: string, reason: string, options: AssistOption[]): void {
    const car = this.cars.get(vin)
    if (car) {
      car.stoppedForAssist = true
      car.speed_kmh = 0
    }
    const frames = Array.from({ length: 8 }, (_, i) => placeholderFrame(`${case_id}:${i}`))
    this.assists.set(case_id, { vin, options: [...options], frames })
    this.emit({
      event: 'assist.request',
      case_id,
      vin,
      location: car?.location ?? '',
      reason,
      options: [...options],
      clip_frames: frames,
    })
  }

  /**
   * Spawns a prop.
   * @param id - Prop id.
   * @param kind - Prop kind.
   * @param x - Map x.
   * @param y - Map y.
   * @param yaw - Degrees.
   */
  spawnProp(id: string, kind: PropKind, x: number, y: number, yaw: number): void {
    this.props.set(id, { id, kind, x, y, yaw })
  }

  /**
   * Removes a prop.
   * @param id - Prop id.
   */
  removeProp(id: string): void {
    this.props.delete(id)
  }

  /**
   * Stores an artifact in memory.
   * @param uri - Artifact URI.
   * @param blob - Content.
   */
  async storeArtifact(uri: string, blob: Blob): Promise<void> {
    this.artifacts.set(uri, blob)
  }

  // --- Ports ----------------------------------------------------------------------------------

  /**
   * Gate capture (placeholder frames).
   * @param vin - Car VIN.
   * @param views - Views to render.
   * @returns One frame per view (unrequested views are empty strings).
   */
  async capture(vin: string, views: GateView[]): Promise<GateFrames> {
    const frames: GateFrames = { front: '', rear: '', left: '', right: '' }
    for (const view of views) frames[view] = placeholderFrame(`${vin}:gate:${view}`)
    const car = this.cars.get(vin)
    if (car) this.passes[vin] = car.speed_kmh
    return frames
  }

  /**
   * Evidence buffer (8 placeholder frames).
   * @param vin - Car VIN.
   * @param camera - Evidence camera.
   * @returns base64 frames.
   */
  getEvidenceFrames(vin: string, camera: 'front' | 'rear'): string[] {
    return Array.from({ length: 8 }, (_, i) => placeholderFrame(`${vin}:${camera}:${i}`))
  }

  /**
   * Other cars and props within 15m of the slot.
   * @param vin - Car being parked (excluded).
   * @param slot - Target slot.
   * @returns Obstacle boxes.
   */
  getObstacles(vin: string, slot: ParkingSlot): ObstacleBox[] {
    const near = (x: number, y: number) => Math.hypot(x - slot.x, y - slot.y) <= OBSTACLE_RANGE_M
    const boxes: ObstacleBox[] = []
    for (const car of this.cars.values()) {
      if (car.vin === vin || !near(car.x, car.y)) continue
      boxes.push({
        id: car.vin,
        x: car.x,
        y: car.y,
        yaw: car.yaw,
        length: CAR_LENGTH_M,
        width: CAR_WIDTH_M,
        moving: car.speed_kmh > 0,
      })
    }
    for (const prop of this.props.values()) {
      if (!near(prop.x, prop.y)) continue
      boxes.push({ id: prop.id, x: prop.x, y: prop.y, yaw: prop.yaw, ...PROP_SIZE[prop.kind] })
    }
    return boxes
  }

  /**
   * Runs control steps with the kinematic bicycle model (exact arcs, no safety stop).
   * @param vin - Car VIN.
   * @param steps - Control steps.
   * @returns Final pose; `stopped` is always false.
   */
  async execute(vin: string, steps: ControlStep[]): Promise<{ pose: Pose; stopped: boolean }> {
    const car = this.cars.get(vin)
    if (!car) return { pose: { x: 0, y: 0, yaw: 0 }, stopped: false }
    for (const step of steps) {
      const s = step.speed_mps * step.duration_s * (step.gear === 'R' ? -1 : 1)
      const k = Math.tan(Math.max(-1, Math.min(1, step.wheel)) * MAX_STEER) / WHEELBASE_M
      const th = car.yaw * DEG
      if (Math.abs(k) < 1e-9) {
        car.x += s * Math.cos(th)
        car.y += s * Math.sin(th)
      } else {
        const nth = th + s * k
        car.x += (Math.sin(nth) - Math.sin(th)) / k
        car.y -= (Math.cos(nth) - Math.cos(th)) / k
        car.yaw = nth / DEG
      }
    }
    const pose = { x: car.x, y: car.y, yaw: car.yaw }
    const log = this.parkingLog[vin] ?? []
    log.push({ steps: steps.length, pose })
    this.parkingLog[vin] = log
    return { pose, stopped: false }
  }

  // --- Truth hints for the offline AI ---------------------------------------------------------

  /**
   * Gate hints: the actual paint colour seen by every view.
   * @param vin - Car VIN.
   * @returns Per-view hint, e.g. 'colour=red'.
   */
  gateHints(vin: string): Partial<Record<GateView, string>> {
    const car = this.cars.get(vin)
    if (!car) return {}
    const colour = `colour=${rgbToColourName(car.rgb)}`
    return Object.fromEntries(GATE_VIEWS.map((view) => [view, colour]))
  }

  /**
   * Evidence hint: what the camera actually sees of the lamps.
   * @param vin - Car VIN.
   * @param camera - Evidence camera.
   * @returns e.g. 'headlights=off'.
   */
  evidenceHint(vin: string, camera: 'front' | 'rear'): string {
    const faults = this.cars.get(vin)?.faults ?? new Set<LampFault>()
    const lamp: LampFault = camera === 'front' ? 'headlights' : 'brake_lights'
    return `${lamp}=${faults.has(lamp) ? 'off' : 'on'}`
  }

  /**
   * Assist hint: workers near the stopped car.
   * @param case_id - Case id.
   * @returns e.g. 'worker=1'.
   */
  assistHint(case_id: string): string {
    const car = this.cars.get(this.assists.get(case_id)?.vin ?? '')
    if (!car) return 'worker=0'
    const workers = [...this.props.values()].filter(
      (prop) => prop.kind === 'worker' && Math.hypot(prop.x - car.x, prop.y - car.y) <= 25,
    )
    return `worker=${workers.length}`
  }

  // --- Inspection helpers for tests and the demo ---------------------------------------------

  /**
   * Spawned props.
   * @returns Props in spawn order.
   */
  listProps(): ScriptedProp[] {
    return [...this.props.values()]
  }

  /**
   * Current mission of a car.
   * @param vin - Car VIN.
   * @returns The running mission.go command, or null.
   */
  missionOf(vin: string): MissionGoCmd | null {
    return this.cars.get(vin)?.mission?.cmd ?? null
  }

  /**
   * Faults currently set on a car.
   * @param vin - Car VIN.
   * @returns Fault names.
   */
  faultsOf(vin: string): LampFault[] {
    return [...(this.cars.get(vin)?.faults ?? [])]
  }

  // --- Simulation -----------------------------------------------------------------------------

  /**
   * Advances the simulation.
   * @param dt - Sim seconds.
   */
  tick(dt: number): void {
    this.clock += dt
    for (const car of [...this.cars.values()]) this.tickCar(car, dt)
    this.untangle()
  }

  /**
   * Breaks waiting cycles (two cars head-on on a two-way road, or cars that each block the
   * other at a crossing): the latest-spawned car in the cycle that has room pulls aside so the
   * others can pass, then carries on.
   */
  private untangle(): void {
    const waiting = (car: ScriptedCar | undefined) =>
      car?.mission && !car.mission.steps && car.blockedBy !== null && car.queued_s >= DEADLOCK_S
    for (const start of this.cars.values()) {
      if (!waiting(start)) continue
      const chain: ScriptedCar[] = []
      let car: ScriptedCar | undefined = start
      while (car && waiting(car) && !chain.includes(car)) {
        chain.push(car)
        car = this.cars.get(car.blockedBy ?? '')
      }
      if (!car || car !== start) continue
      const yielders = [...chain].sort((a, b) => b.seq - a.seq)
      for (const yielder of yielders) {
        const index = chain.indexOf(yielder)
        const other = chain[(index + 1) % chain.length]
        if (this.pullAside(yielder, other)) return
      }
    }
  }

  /**
   * Inserts a sideways step out of the way (right first, then left) when that spot is free.
   * @returns True when the car will pull aside.
   */
  private pullAside(car: ScriptedCar, other: ScriptedCar): boolean {
    const mission = car.mission
    if (!mission) return false
    // Replace an unfinished pull-aside rather than stacking them.
    while (mission.path[mission.index]?.gap === PULL_ASIDE_GAP_M)
      mission.path.splice(mission.index, 1)
    const next = mission.path[mission.index]
    const heading = next ? Math.atan2(next.y - car.y, next.x - car.x) / DEG : car.yaw
    const others = [...this.cars.values()].filter((item) => item !== car)
    for (const side of [-1, 1]) {
      const normal = (heading + side * 90) * DEG
      const spot = {
        x: car.x + PULL_ASIDE_M * Math.cos(normal),
        y: car.y + PULL_ASIDE_M * Math.sin(normal),
        yaw: car.yaw,
      }
      let free = true
      for (let s = SAMPLE_M; s <= PULL_ASIDE_M + 1e-9 && free; s += SAMPLE_M) {
        const k = Math.min(s, PULL_ASIDE_M) / PULL_ASIDE_M
        const at = {
          x: car.x + (spot.x - car.x) * k,
          y: car.y + (spot.y - car.y) * k,
          yaw: car.yaw,
        }
        const body = carRect(at, CLEARANCE_M)
        free = !others.some((item) => rectsOverlap(body, carRect(item)))
      }
      if (!free) continue
      const aside: PathNode = {
        ...spot,
        name: null,
        yaw: car.yaw,
        gap: PULL_ASIDE_GAP_M,
        transient: true,
      }
      mission.path.splice(mission.index, 0, aside)
      mission.total_m += 2 * PULL_ASIDE_M
      car.yieldTo = { vin: other.vin, x: spot.x, y: spot.y }
      this.noteQueue(car, null, 0)
      return true
    }
    return false
  }

  private tickCar(car: ScriptedCar, dt: number): void {
    if (car.waitingCase !== null) this.checkCleared(car, dt)
    const mission = car.mission
    if (!mission) return
    if (car.held) {
      if (car.holdLeft !== null) {
        car.holdLeft -= dt
        if (car.holdLeft <= 0) {
          car.held = false
          car.holdLeft = null
        }
      }
      car.speed_kmh = 0
      this.noteQueue(car, null, 0)
      this.emitStatus(car)
      return
    }
    if (car.stoppedForAssist) {
      car.speed_kmh = 0
      this.noteQueue(car, null, 0)
      this.emitStatus(car)
      return
    }
    if (mission.steps) {
      this.tickRoutine(car, mission, dt)
      return
    }
    this.drive(car, mission, dt)
  }

  private drive(car: ScriptedCar, mission: ActiveMission, dt: number): void {
    const path = mission.path
    let reached = false
    // Nodes the car already stands on (or side steps it is already past) are reached without
    // moving.
    while (mission.index < path.length) {
      const node = path[mission.index]
      const after = path[mission.index + 1]
      if (node.around && !node.around.some((id) => this.props.has(id))) {
        mission.index++
        continue
      }
      if (node.transient && after) {
        const dx = after.x - node.x
        const dy = after.y - node.y
        const length = Math.hypot(dx, dy)
        const ahead =
          length > 1e-6 && ((car.x - node.x) * dx + (car.y - node.y) * dy) / length > 0.5
        const closer = Math.hypot(after.x - car.x, after.y - car.y) < length
        if (ahead && closer) {
          mission.index++
          continue
        }
      }
      if (Math.hypot(node.x - car.x, node.y - car.y) > 1e-6) break
      if (node.yaw !== null) car.yaw = node.yaw
      if (node.name !== null) car.location = node.name
      mission.index++
      reached = mission.index >= path.length
    }
    const vmax = Math.min(mission.speed_mps, path[mission.index]?.speed_mps ?? mission.speed_mps)
    if (this.yielding(car)) {
      car.speed_kmh = 0
      this.emitStatus(car)
      return
    }
    // Queue behind whatever is ahead: stop STANDING_GAP_M short of it, slow down proportionally
    // when the clear road is shorter than the headway distance.
    const { free, blocker } = this.clearance(car, mission, vmax * (dt + HEADWAY_S))
    const speed = Math.max(Math.min(CRAWL_MPS, vmax), vmax * Math.min(1, free / (vmax * HEADWAY_S)))
    let remaining = Math.min(speed * dt, free)
    if (remaining < 1e-3) remaining = 0
    this.noteQueue(car, remaining === 0 ? blocker : null, dt)
    const moved = remaining
    while (remaining > 0 && mission.index < path.length) {
      const target = path[mission.index]
      const dx = target.x - car.x
      const dy = target.y - car.y
      const distance = Math.hypot(dx, dy)
      if (target.yaw !== null) car.yaw = target.yaw
      else if (distance > 1e-6) car.yaw = Math.atan2(dy, dx) / DEG
      if (distance <= remaining) {
        car.x = target.x
        car.y = target.y
        if (target.name !== null) car.location = target.name
        mission.index++
        mission.travelled_m += distance
        remaining -= distance
        reached = mission.index >= path.length
      } else {
        car.x += (dx / distance) * remaining
        car.y += (dy / distance) * remaining
        mission.travelled_m += remaining
        remaining = 0
      }
    }
    if (mission.index < path.length && this.arrivesInQueue(car, mission)) {
      // The destination is held by a car that is not going anywhere: this queue position is
      // the car's own standing spot at the destination.
      mission.index = path.length
      mission.travelled_m = mission.total_m
      car.location = mission.cmd.waypoints[mission.cmd.waypoints.length - 1]
    }
    const arrived = mission.index >= path.length
    car.speed_kmh = arrived ? 0 : (moved / dt) * 3.6
    if (reached) car.yaw = point(mission.cmd.waypoints[mission.cmd.waypoints.length - 1]).yaw
    if (arrived) this.noteQueue(car, null, dt)
    this.emitStatus(car)
    if (!arrived || car.mission !== mission) return
    if (mission.cmd.routine) {
      const wanted = mission.cmd.routine_steps
      mission.steps = R12_STEPS.filter((step) => !wanted || wanted.includes(step.step))
      return
    }
    this.finish(car, mission)
  }

  /**
   * True while a car that pulled aside waits at its spot for the car it let pass.
   */
  private yielding(car: ScriptedCar): boolean {
    const yieldTo = car.yieldTo
    if (!yieldTo) return false
    const other = this.cars.get(yieldTo.vin)
    const gone = !other?.mission || Math.hypot(other.x - car.x, other.y - car.y) > YIELD_CLEAR_M
    if (gone) {
      car.yieldTo = null
      return false
    }
    if (Math.hypot(yieldTo.x - car.x, yieldTo.y - car.y) > 0.05) return false
    this.noteQueue(car, other, 0)
    return true
  }

  /** Records who a car is queued behind (null when it moved). */
  private noteQueue(car: ScriptedCar, blocker: ScriptedCar | null, dt: number): void {
    car.blockedBy = blocker?.vin ?? null
    car.queued_s = blocker ? car.queued_s + dt : 0
    if (blocker && !blocker.mission) car.idleQueue_s += dt
    else car.idleQueue_s = 0
  }

  /**
   * True when a car has queued for {@link IDLE_QUEUE_S} behind a car with no mission and is
   * within {@link QUEUE_ARRIVAL_M} of its destination: it then arrives where it stands.
   */
  private arrivesInQueue(car: ScriptedCar, mission: ActiveMission): boolean {
    if (car.idleQueue_s < IDLE_QUEUE_S) return false
    return mission.total_m - mission.travelled_m <= QUEUE_ARRIVAL_M
  }

  /**
   * Distance the car can drive along its path before its footprint (plus the standing gap in
   * front) would touch another car, capped at `lookahead`.
   * @returns Free distance and the first car in the way (null when the road is clear).
   */
  private clearance(
    car: ScriptedCar,
    mission: ActiveMission,
    lookahead: number,
  ): { free: number; blocker: ScriptedCar | null } {
    const here = carRect(car)
    const others = [...this.cars.values()].filter(
      // A car already touching this one (spawned or parked into it) is driven away from, not
      // queued behind, so both can separate.
      (other) => other !== car && !rectsOverlap(here, carRect(other)),
    )
    // Props (a box in the lane, a forklift scene) are not driven through either: the car stops
    // short of them until they are cleared or it is sent around them (pass / reroute).
    const props = [...this.props.values()]
      .map((prop): Rect => ({ x: prop.x, y: prop.y, yaw: prop.yaw, ...PROP_SIZE[prop.kind] }))
      .filter((rect) => !rectsOverlap(here, rect))
    if (others.length === 0 && props.length === 0) {
      return { free: Number.POSITIVE_INFINITY, blocker: null }
    }
    const shoulder = new Set(others.filter((other) => !other.mission && onShoulder(other)))
    const legs = this.legs(car, mission, lookahead)
    let clear = 0
    for (const leg of legs) {
      for (let s = leg.from + SAMPLE_M; ; s += SAMPLE_M) {
        const at = Math.min(s, leg.to)
        const t = (at - leg.from) / Math.max(leg.length, 1e-9)
        const pose = {
          x: leg.ax + (leg.bx - leg.ax) * t,
          y: leg.ay + (leg.by - leg.ay) * t,
          yaw: leg.yaw,
        }
        const body = carRect(pose, CLEARANCE_M)
        const front = gapBox(pose, leg.heading, leg.gap)
        const hit = others.find((other) => {
          const rect = carRect(other)
          // Cars parked on the shoulder (yard slots, rework stands) are not in the lane: only
          // touching them counts, not having them inside the gap ahead.
          return rectsOverlap(body, rect) || (!shoulder.has(other) && rectsOverlap(front, rect))
        })
        if (hit) return { free: clear, blocker: hit }
        if (props.some((rect) => rectsOverlap(body, rect) || rectsOverlap(front, rect))) {
          return { free: clear, blocker: null }
        }
        clear = at
        if (at >= leg.to) break
      }
    }
    return { free: Number.POSITIVE_INFINITY, blocker: null }
  }

  /** The car's remaining path as straight legs, up to `lookahead` metres ahead. */
  private legs(car: ScriptedCar, mission: ActiveMission, lookahead: number): Leg[] {
    const legs: Leg[] = []
    let ax = car.x
    let ay = car.y
    let from = 0
    for (let i = mission.index; i < mission.path.length && from < lookahead; i++) {
      const node = mission.path[i]
      const length = Math.hypot(node.x - ax, node.y - ay)
      if (length < 1e-6) continue
      const heading = Math.atan2(node.y - ay, node.x - ax) / DEG
      const to = Math.min(from + length, lookahead)
      const k = (to - from) / length
      legs.push({
        ax,
        ay,
        bx: ax + (node.x - ax) * k,
        by: ay + (node.y - ay) * k,
        from,
        to,
        length: to - from,
        heading,
        yaw: node.yaw ?? heading,
        gap: node.gap ?? STANDING_GAP_M,
      })
      ax = node.x
      ay = node.y
      from += length
    }
    return legs
  }

  private tickRoutine(car: ScriptedCar, mission: ActiveMission, dt: number): void {
    const steps = mission.steps ?? []
    mission.stepElapsed += dt
    while (mission.stepElapsed >= this.stepSeconds && mission.stepIndex < steps.length) {
      mission.stepElapsed -= this.stepSeconds
      const step = steps[mission.stepIndex]
      mission.stepIndex++
      this.emit({
        event: 'track.step',
        vin: car.vin,
        mission_id: mission.cmd.mission_id,
        routine: mission.cmd.routine ?? 'R-12',
        step: step.step,
        name: step.name,
        telemetry: { ...step.telemetry },
      })
      if (car.mission !== mission) return
    }
    if (mission.stepIndex >= steps.length) this.finish(car, mission)
  }

  private finish(car: ScriptedCar, mission: ActiveMission): void {
    car.mission = null
    car.speed_kmh = 0
    this.emit({
      event: 'mission.done',
      mission_id: mission.cmd.mission_id,
      vin: car.vin,
      location: car.location,
    })
  }

  private startMission(car: ScriptedCar, cmd: MissionGoCmd): void {
    const path = buildPath(car, cmd.waypoints, this.freeStand(car))
    let total = 0
    let from = { x: car.x, y: car.y }
    for (const node of path) {
      total += Math.hypot(node.x - from.x, node.y - from.y)
      from = node
    }
    car.mission = {
      cmd,
      path,
      index: 0,
      speed_mps: (cmd.max_speed_kmh ?? 10) / 3.6,
      total_m: total,
      travelled_m: 0,
      steps: null,
      stepIndex: 0,
      stepElapsed: 0,
    }
    car.held = false
    car.holdLeft = null
    car.yieldTo = null
    this.noteQueue(car, null, 0)
  }

  /**
   * First rework stand nobody stands on or drives to (stand 0 when the bay is full; the car
   * then queues for it).
   */
  private freeStand(car: ScriptedCar): number {
    for (let k = 0; k < REWORK_STANDS; k++) {
      const { stand } = reworkStand(k)
      const taken = [...this.cars.values()].some((other) => {
        if (other === car) return false
        const end = other.mission?.path.at(-1)
        const heading = end?.name === REWORK && Math.hypot(end.x - stand.x, end.y - stand.y) < 0.5
        return heading || Math.hypot(other.x - stand.x, other.y - stand.y) < 0.5
      })
      if (!taken) return k
    }
    return 0
  }

  private choose(cmd: AssistChooseCmd): void {
    const record = this.assists.get(cmd.case_id)
    if (!record?.options.includes(cmd.option)) return
    this.assistOutcomes[cmd.case_id] = { option: cmd.option, chosen_by: cmd.chosen_by }
    const car = this.cars.get(record.vin)
    if (!car) return
    if (cmd.option === 'wait_for_removal') {
      car.waitingCase = cmd.case_id
      car.waited_s = 0
      this.checkCleared(car, 0)
      return
    }
    car.stoppedForAssist = false
    if (cmd.option === 'pass_left_3kmh') this.detour(car, 1, PASS_OFFSET_M, PASS_SPEED_MPS)
    else this.reroute(car)
  }

  /**
   * 'reroute_lane_c': a car still heading for the finishing corner goes via lane C instead;
   * otherwise it takes the lane on the right around the obstruction at mission speed.
   */
  private reroute(car: ScriptedCar): void {
    const mission = car.mission
    if (!mission) return
    const index = mission.path.findIndex(
      (node, i) => i >= mission.index && node.name === 'finishing',
    )
    if (index < 0) {
      this.detour(car, -1, REROUTE_OFFSET_M, mission.speed_mps)
      return
    }
    const lane = POINTS.lane_c
    mission.path[index] = { x: lane.x, y: lane.y, name: 'lane_c', yaw: null }
    mission.cmd = {
      ...mission.cmd,
      waypoints: mission.cmd.waypoints.map((name) => (name === 'finishing' ? 'lane_c' : name)),
    }
  }

  /**
   * Sends a car around the props in front of it: out to `offset` metres on one side (+1 left,
   * -1 right) before the first prop, along it past the last one, then back to its route.
   * @param car - Car stopped in front of an obstruction.
   * @param side - +1 left, -1 right.
   * @param offset - Lateral offset (m).
   * @param speed - Speed while passing (m/s).
   */
  private detour(car: ScriptedCar, side: 1 | -1, offset: number, speed: number): void {
    const mission = car.mission
    const next = mission?.path[mission.index]
    if (!mission || !next) return
    const heading = Math.atan2(next.y - car.y, next.x - car.x)
    const fx = Math.cos(heading)
    const fy = Math.sin(heading)
    const blocking = [...this.props.values()]
      .filter((prop) => Math.hypot(prop.x - car.x, prop.y - car.y) <= this.clearRadius)
      .map((prop) => {
        const dx = prop.x - car.x
        const dy = prop.y - car.y
        return { id: prop.id, ahead: dx * fx + dy * fy, lateral: Math.abs(-dx * fy + dy * fx) }
      })
      .filter((item) => item.ahead > 0 && item.lateral < offset)
    if (blocking.length === 0) return
    const along = blocking.map((item) => item.ahead)
    const around = blocking.map((item) => item.id)
    const reach = Math.hypot(next.x - car.x, next.y - car.y)
    const start = Math.min(reach, Math.max(1, Math.min(...along) - CAR_LENGTH_M))
    const end = Math.min(reach, Math.max(...along) + CAR_LENGTH_M)
    // The asked-for side first; the other side when a car stands there; none when both are
    // taken (the car then waits for the crew to clear the obstruction).
    for (const way of [side, -side]) {
      const lx = -fy * way * offset
      const ly = fx * way * offset
      const nodes: PathNode[] = [start, end].map((ahead) => ({
        x: car.x + fx * ahead + lx,
        y: car.y + fy * ahead + ly,
        name: null,
        yaw: null,
        speed_mps: speed,
        transient: true,
        around,
      }))
      if (!this.routeClear(car, nodes)) continue
      mission.path.splice(mission.index, 0, ...nodes)
      mission.total_m += 2 * offset
      return
    }
  }

  /** True when the car's footprint can follow `nodes` without touching another car. */
  private routeClear(car: ScriptedCar, nodes: { x: number; y: number }[]): boolean {
    const others = [...this.cars.values()].filter((other) => other !== car).map((o) => carRect(o))
    let from = { x: car.x, y: car.y }
    for (const node of nodes) {
      const length = Math.hypot(node.x - from.x, node.y - from.y)
      const yaw = Math.atan2(node.y - from.y, node.x - from.x) / DEG
      for (let d = 0; d <= length; d += SAMPLE_M) {
        const k = length > 0 ? d / length : 0
        const pose = { x: from.x + (node.x - from.x) * k, y: from.y + (node.y - from.y) * k, yaw }
        const body = carRect(pose, CLEARANCE_M)
        if (others.some((rect) => rectsOverlap(body, rect))) return false
      }
      from = node
    }
    return true
  }

  private checkCleared(car: ScriptedCar, dt: number): void {
    const blocked = [...this.props.values()].some(
      (prop) => Math.hypot(prop.x - car.x, prop.y - car.y) <= this.clearRadius,
    )
    if (blocked) {
      car.waited_s += dt
      return
    }
    const case_id = car.waitingCase
    if (case_id !== null) {
      const outcome = this.assistOutcomes[case_id] as Record<string, unknown> | undefined
      this.assistOutcomes[case_id] = { ...outcome, waited_s: car.waited_s }
    }
    car.waitingCase = null
    car.stoppedForAssist = false
  }

  private emitStatus(car: ScriptedCar): void {
    const mission = car.mission
    const total = mission?.total_m ?? 0
    this.emit({
      event: 'status',
      vin: car.vin,
      location: car.location,
      x: car.x,
      y: car.y,
      speed_kmh: car.speed_kmh,
      mission_id: mission?.cmd.mission_id ?? '',
      progress: mission && total > 0 ? Math.min(1, mission.travelled_m / total) : 1,
      held: car.held,
      stopped_for_assist: car.stoppedForAssist,
    })
  }

  private emit(event: SimEvent): void {
    for (const cb of [...(this.listeners.get(event.event) ?? [])]) cb(event)
  }
}
