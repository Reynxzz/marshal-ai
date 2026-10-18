// ThreeJsBackend driving system (TEAM_PLAN M2.6): owns the cars, routes missions over the lane
// graph, steps the driver at a fixed 0.1 s and emits status (1 Hz) and mission.done. Implements
// the Marshal DrivingSystem contract. Pure simulation: no three.js import, so tests run headless
// and the viewer reads poses. Gate / evidence / parking ports land in M3 / M5 / M7.
//
// Multi-car (M4): queueing keeps a 6 m standing gap plus a rolling headway behind lane traffic,
// pull-overs approach at walking pace and square up onto the exact point pose, departing cars
// pull out 5 m into the lane, and slot-to-slot moves drive forward (<= 40 m) or reverse (<= 30 m).
// shortcut: square-up snaps the pose (TEAM_PLAN allows it); static parked cars are not lane
// obstacles yet (M4.4 collision yield + M7 obstacle boxes land with parking); reversing backs up
// straight without the reverse-turn-in variant.

import { isPointName, type NamedPoint, type PointName, YARD_SLOTS } from '#/lib/marshal/points'
import { rgbToColourName } from '#/lib/marshal/state'
import type {
  AssistOption,
  AssistRequestEvent,
  ControlStep,
  DrivingSystem,
  GateView,
  MissionDoneEvent,
  MissionGoCmd,
  ObstacleBox,
  ParkingSlot,
  Pose,
  SimCommand,
  SimEvent,
  StatusEvent,
  TrackStepEvent,
} from '#/lib/marshal/types'
import { CAR_LENGTH_M, CAR_WIDTH_M, type CarLamps } from './actors/car'
import {
  ARRIVAL_RADIUS_M,
  approachSpeed,
  BRAKE_MPS2,
  controlStepDelta,
  FIXED_STEP_S,
  GATE_RECORD_RADIUS_M,
  GATE_SLOW_ZONE_M,
  GATE_SPEED_MPS,
  LOOKAHEAD_M,
  MAX_ACCEL_MPS2,
  nearestIndex,
  type PathPoint,
  PRECISE_TIMEOUT_S,
  pointAhead,
  purePursuitSteer,
  SAFETY_CLEARANCE_M,
  SAFETY_PROBE_M,
  stepVehicle,
} from './actors/driver'
import { FLAT_PROP_HEIGHT_M, PROP_FOOTPRINTS, type PropKind } from './actors/props'
import {
  offLamps,
  R12_BLINK_PERIOD_S,
  R12_BRAKE_TEST_KMH,
  R12_REVERSE_M,
  type RoutineStepSpec,
  r12Steps,
} from './actors/routine'
import { createLaneGraph, type LaneGraph } from './world/plantMap'
import { sampleCenterline } from './world/roadBuilder'

/** Default mission speed (TEAM_PLAN: max 10 km/h in the plant). */
export const DEFAULT_SPEED_KMH = 10
/** Status event period, seconds (TEAM_PLAN M2.5: 1 Hz). */
export const STATUS_PERIOD_S = 1
/** Final mission waypoints that need the pull-over approach (TEAM_PLAN M2.4/M4.2). */
export const PRECISE_WAYPOINTS: ReadonlySet<string> = new Set([...YARD_SLOTS, 'rework.electrical'])

/** Queuing (TEAM_PLAN M4.1): centre-to-centre gap standing, plus a rolling headway. */
export const QUEUE_GAP_M = 6
export const QUEUE_HEADWAY_S = 1
/** Pull-over approach (TEAM_PLAN M4.2): walking pace from the run distance, square up at 3 m. */
export const PULL_OVER_RUN_M = 12
export const PULL_OVER_SPEED_MPS = 1.4
export const PULL_OVER_AIM_M = 4
export const SQUARE_UP_RADIUS_M = 3
/** Slot departure (TEAM_PLAN M4.3): leave from the lane point this far ahead. */
export const PULL_OUT_M = 5
/** Slot-to-slot moves (TEAM_PLAN M4.4): direct when within these distances. */
export const SLOT_MOVE_FORWARD_LIMIT_M = 40
export const SLOT_MOVE_REVERSE_LIMIT_M = 30
export const REVERSE_SPEED_MPS = 1
/** Queue check corridor and lookahead, metres. */
const QUEUE_LATERAL_M = 2.5
const QUEUE_LOOKAHEAD_M = 40
/** A car within this of a pull-over point is parked, not queueing. */
const PULLOVER_RADIUS_M = 2

/** Obstacle scan (TEAM_PLAN M6.2): lookahead = base + v^2/(2*decel), lateral corridor. */
export const OBSTACLE_BASE_M = 9
export const PROP_DECEL_MPS2 = 4
const OBSTACLE_LATERAL_M = 1.8
/** Stop this far short of a blocking prop. */
const PROP_STOP_GAP_M = 2
/** Blocked detection (TEAM_PLAN M6.3): still for this long, obstacle this close ahead. */
export const BLOCKED_TIME_S = 5
export const BLOCKED_SPEED_MPS = 0.6 / 3.6
const BLOCKED_RADIUS_M = 14
const BLOCKED_LATERAL_M = 3
/** Waiting for removal clears when no prop is within this. */
const CLEAR_RADIUS_M = 15
/** Pass-left maneuver (TEAM_PLAN M6.4): back up for room, crawl at 3 km/h, this offset, return. */
export const AVOID_SPEED_MPS = 3 / 3.6
export const AVOID_OFFSET_M = 3.5
export const AVOID_BACKUP_M = 6
export const AVOID_BACKUP_SPEED_MPS = 1
/** The offset is held until the obstacle is this far behind / outside this corridor. */
const AVOID_TRACK_RANGE_M = 12
const AVOID_TRACK_LATERAL_M = 6
/** Obstacles within this of a parking slot are reported (M7.2). */
const PARKING_OBSTACLE_RANGE_M = 15

type LampFault = 'headlights' | 'brake_lights' | 'indicators'

/** Routine state while a mission runs its R-12 sequence at the final waypoint (M5.2). */
type ActiveRoutine = {
  id: string
  steps: RoutineStepSpec[]
  index: number
  /** Seconds inside the current step. */
  elapsed: number
  /** Pose the current motion step started from. */
  startPose: { x: number; y: number }
  /** Speed when the brake test hit the brakes, null before that. */
  brakingFrom: number | null
}

type ActiveMission = {
  cmd: MissionGoCmd
  waypoints: PointName[]
  /** Next waypoint still to be passed (intermediate ones never stop the car). */
  waypointIndex: number
  route: PathPoint[]
  routeIndex: number
  /** Final waypoint is a pull-over: approach at walking pace, then square up on the pose. */
  precise: boolean
  /** Slot-to-slot move driven in reverse. */
  reverse: boolean
  /** Gate speed already recorded for this mission (first crossing wins). */
  gateRecorded: boolean
  preciseElapsed: number
  travelled_m: number
  total_m: number
  /** R-12 sequence running at the final waypoint (null outside the test track). */
  routine: ActiveRoutine | null
}

type EngineCar = {
  vin: string
  rgb: [number, number, number]
  x: number
  y: number
  /** Radians, CARLA frame. */
  yaw: number
  speed_mps: number
  /** Last named point passed. */
  location: string
  mission: ActiveMission | null
  held: boolean
  holdLeft: number | null
  stoppedForAssist: boolean
  faults: Set<LampFault>
  /** Lamp state for the viewer (faults darken a lamp visually, telemetry stays nominal). */
  lamps: CarLamps
  /** Assist case the car waits on with `wait_for_removal`. */
  waitingCase: string | null
  waited_s: number
  /** Seconds the car has been still with a non-plant obstacle ahead (M6.3). */
  blockedFor: number
  /** An assist case is open: do not raise another (M6.3). */
  assistPending: boolean
  /** Assists raised for this car, for `AC-<vin>-<seq>` ids. */
  assistSeq: number
  /** Pass-left maneuver phase (M6.4): back up for room, then swerve past. */
  avoidPhase: 'backup' | 'swerve' | null
  /** Pose the backup phase started from. */
  avoidStart: { x: number; y: number } | null
  /** Assist case the pass-left maneuver belongs to. */
  avoidCase: string | null
  /** Closest distance to a prop during the pass-left maneuver. */
  avoidClosest: number
}

type AssistRecord = { vin: string; options: AssistOption[]; frames: string[] }
type PropState = { id: string; kind: PropKind; x: number; y: number; yaw: number }
type Listener = (event: any) => void

/** Engine options. */
export type EngineOptions = {
  /** Chase-camera clip for an assist case (M6.3/M6.5); [] when no rig is attached. */
  assistFrames?: (vin: string) => string[]
}

function routeLength(route: readonly PathPoint[]): number {
  let total = 0
  for (let index = 1; index < route.length; index++) {
    total += Math.hypot(route[index].x - route[index - 1].x, route[index].y - route[index - 1].y)
  }
  return total
}

/**
 * Distance from a point to an oriented obstacle box (M7.3), metres; 0 inside the box.
 * @param x - Probe x, CARLA frame.
 * @param y - Probe y, CARLA frame.
 * @param box - Obstacle box.
 * @returns Clearance to the box surface.
 */
function distanceToBox(x: number, y: number, box: ObstacleBox): number {
  const yaw = (box.yaw * Math.PI) / 180
  const dx = x - box.x
  const dy = y - box.y
  const localX = dx * Math.cos(yaw) + dy * Math.sin(yaw)
  const localY = -dx * Math.sin(yaw) + dy * Math.cos(yaw)
  const outsideX = Math.max(Math.abs(localX) - box.length / 2, 0)
  const outsideY = Math.max(Math.abs(localY) - box.width / 2, 0)
  return Math.hypot(outsideX, outsideY)
}

/** IndexedDB database for evidence artifacts (M10.7). */
const ARTIFACT_DB = 'marshal-artifacts'
const ARTIFACT_STORE = 'artifacts'
let artifactDb: Promise<IDBDatabase> | null = null

function openArtifactDb(): Promise<IDBDatabase> {
  artifactDb ??= new Promise((resolve, reject) => {
    const request = indexedDB.open(ARTIFACT_DB, 1)
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(ARTIFACT_STORE)) db.createObjectStore(ARTIFACT_STORE)
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('artifact db open failed'))
  })
  return artifactDb
}

function putArtifact(uri: string, blob: Blob): Promise<void> {
  return openArtifactDb().then(
    (db) =>
      new Promise<void>((resolve, reject) => {
        const tx = db.transaction(ARTIFACT_STORE, 'readwrite')
        tx.objectStore(ARTIFACT_STORE).put(blob, uri)
        tx.oncomplete = () => resolve()
        tx.onerror = () => reject(tx.error ?? new Error('artifact put failed'))
      }),
  )
}

/**
 * Deterministic driving engine over the lane graph. Cars follow sampled centre-lines with
 * pure-pursuit steering; the orchestrator drives it with `mission.go` / `mission.hold` /
 * `mission.resume`.
 */
export class ThreeJsEngine implements DrivingSystem {
  /** Commands received, oldest first. */
  readonly commands: SimCommand[] = []
  /** vin -> km/h as it crossed the gate (M2.4). */
  readonly passes: Record<string, number> = {}
  readonly assistOutcomes: Record<string, unknown> = {}
  readonly parkingLog: Record<string, unknown[]> = {}

  private readonly graph: LaneGraph
  /** Yard slots plus the rework bay: pull-over points (M4.1/M4.2). */
  private readonly pullOvers: NamedPoint[]
  private readonly cars = new Map<string, EngineCar>()
  private readonly listeners = new Map<SimEvent['event'], Set<Listener>>()
  private readonly assists = new Map<string, AssistRecord>()
  private readonly props = new Map<string, PropState>()
  /** Optional chase-camera clip for assist cases (M6.3); [] without a camera rig. */
  private readonly assistFrames: (vin: string) => string[]
  private readonly artifacts = new Map<string, Blob>()
  private clock = 0
  private statusClock = 0

  /**
   * @param graph - Lane graph (defaults to the Marshal contract map).
   * @param options - Optional assist clip provider (the M5.1 chase camera).
   */
  constructor(graph: LaneGraph = createLaneGraph(), options: EngineOptions = {}) {
    this.graph = graph
    const pullOverNames: PointName[] = [...YARD_SLOTS, 'rework.electrical']
    this.pullOvers = pullOverNames.map((name) => graph.points[name])
    this.assistFrames = options.assistFrames ?? (() => [])
  }

  /** Sim time in seconds. */
  get time(): number {
    return this.clock
  }

  // --- Commands -------------------------------------------------------------------------------

  /**
   * Executes an orchestrator command.
   * @param cmd - Command to run.
   */
  send(cmd: SimCommand): void {
    this.commands.push(cmd)
    const car = this.cars.get(cmd.vin)
    if (!car) return
    if (cmd.op === 'mission.go') this.startMission(car, cmd)
    else if (cmd.op === 'mission.hold') {
      car.held = true
      car.holdLeft = cmd.duration_s ?? null
    } else if (cmd.op === 'mission.resume') {
      car.held = false
      car.holdLeft = null
    } else {
      // assist.choose (M6.4): record the decision and run the chosen manoeuvre.
      this.assistOutcomes[cmd.case_id] = { option: cmd.option, chosen_by: cmd.chosen_by }
      car.assistPending = false
      car.stoppedForAssist = false
      car.blockedFor = 0
      if (cmd.option === 'wait_for_removal') {
        car.waitingCase = cmd.case_id
        car.waited_s = 0
      } else if (cmd.option === 'pass_left_3kmh') {
        car.avoidPhase = 'backup'
        car.avoidStart = { x: car.x, y: car.y }
        car.avoidCase = cmd.case_id
        car.avoidClosest = Number.POSITIVE_INFINITY
      } else if (!this.rerouteViaLaneC(car)) {
        // No lane C on the way: fall back to passing the obstacle.
        car.avoidPhase = 'backup'
        car.avoidStart = { x: car.x, y: car.y }
        car.avoidCase = cmd.case_id
        car.avoidClosest = Number.POSITIVE_INFINITY
      }
    }
  }

  // --- Events ---------------------------------------------------------------------------------

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
    const at = this.point(opts.at)
    const back = opts.behind_m ?? 0
    const yaw = (at.yaw * Math.PI) / 180
    this.cars.set(vin, {
      vin,
      rgb: opts.colour,
      x: at.x - back * Math.cos(yaw),
      y: at.y - back * Math.sin(yaw),
      yaw,
      speed_mps: 0,
      location: opts.at,
      mission: null,
      held: false,
      holdLeft: null,
      stoppedForAssist: false,
      faults: new Set(),
      lamps: offLamps(),
      waitingCase: null,
      waited_s: 0,
      blockedFor: 0,
      assistPending: false,
      assistSeq: 0,
      avoidPhase: null,
      avoidStart: null,
      avoidCase: null,
      avoidClosest: Number.POSITIVE_INFINITY,
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
   * Sets lamp faults (telemetry keeps reporting ON, per TEAM_PLAN M5.3).
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
   * Lamp faults set on a car (visual overrides, M5.3).
   * @param vin - Car VIN.
   * @returns Fault names.
   */
  faultsOf(vin: string): LampFault[] {
    return [...(this.cars.get(vin)?.faults ?? [])]
  }

  /**
   * Lamp state of a car for the viewer (routine steps drive it).
   * @param vin - Car VIN.
   * @returns Copy of the lamp state.
   */
  lampsOf(vin: string): CarLamps {
    return { ...(this.cars.get(vin)?.lamps ?? offLamps()) }
  }

  /**
   * Current car pose.
   * @param vin - Car VIN.
   * @returns Pose in the map frame (zeros for unknown cars).
   */
  getPositionXY(vin: string): { x: number; y: number; yaw: number } {
    const car = this.cars.get(vin)
    if (!car) return { x: 0, y: 0, yaw: 0 }
    return { x: car.x, y: car.y, yaw: (car.yaw * 180) / Math.PI }
  }

  /**
   * Named point transform.
   * @param name - Named point.
   * @returns Point pose; throws for unknown points.
   */
  getTransform(name: string): { x: number; y: number; yaw: number } {
    const point = this.point(name)
    return { x: point.x, y: point.y, yaw: point.yaw }
  }

  /**
   * Gate truth hints for the offline AI: the paint colour seen by every view.
   * @param vin - Car VIN.
   * @returns Per-view hint, e.g. 'colour=red'.
   */
  gateHints(vin: string): Partial<Record<GateView, string>> {
    const car = this.cars.get(vin)
    if (!car) return {}
    const colour = `colour=${rgbToColourName(car.rgb)}`
    return { front: colour, rear: colour, left: colour, right: colour }
  }

  /**
   * Lamp evidence hint for the offline AI (faults, not telemetry).
   * @param vin - Car VIN.
   * @param camera - Evidence camera.
   * @returns e.g. 'headlights=off'.
   */
  evidenceHint(vin: string, camera: 'front' | 'rear'): string {
    const lamp: LampFault = camera === 'front' ? 'headlights' : 'brake_lights'
    const off = this.cars.get(vin)?.faults.has(lamp) ?? false
    return `${lamp}=${off ? 'off' : 'on'}`
  }

  /**
   * Assist truth hint for the offline AI: workers near the stopped car.
   * @param case_id - Assist case.
   * @returns e.g. 'worker=1'.
   */
  assistHint(case_id: string): string {
    const record = this.assists.get(case_id)
    const car = record ? this.cars.get(record.vin) : undefined
    if (!car) return 'worker=0'
    let workers = 0
    for (const prop of this.props.values()) {
      if (prop.kind !== 'worker') continue
      if (Math.hypot(prop.x - car.x, prop.y - car.y) <= 25) workers++
    }
    return `worker=${workers}`
  }

  /**
   * Stored clip for an assist case (empty until the evidence camera lands in M5).
   * @param case_id - Case id.
   * @returns base64 frames.
   */
  getAssistMedia(case_id: string): string[] {
    return [...(this.assists.get(case_id)?.frames ?? [])]
  }

  /**
   * Obstacles around a parking slot (M7.2): vehicles and props, flat props filtered out.
   * @param vin - Car being parked (excluded from the result).
   * @param slot - Target slot.
   * @returns Obstacle boxes in the map frame.
   */
  getObstacles(vin: string, slot: ParkingSlot): ObstacleBox[] {
    return this.nearbyObstacles(vin, slot.x, slot.y)
  }

  /**
   * Runs a parking control sequence with the kinematic bicycle model and the M7.3 safety stop.
   * @param vin - Car being parked.
   * @param steps - Control steps.
   * @returns Final pose (yaw degrees) and whether the safety stop fired.
   */
  async execute(vin: string, steps: ControlStep[]): Promise<{ pose: Pose; stopped: boolean }> {
    const car = this.cars.get(vin)
    if (!car) return { pose: { x: 0, y: 0, yaw: 0 }, stopped: false }
    const obstacles = this.nearbyObstacles(vin, car.x, car.y)
    let stopped = false
    for (const step of steps) {
      if (this.safetyStop(car, step, obstacles)) {
        stopped = true
        break
      }
      const delta = controlStepDelta(car, step)
      car.x += delta.dx
      car.y += delta.dy
      car.yaw += delta.dyaw
    }
    car.speed_mps = 0
    const pose = this.getPositionXY(vin)
    const log = this.parkingLog[vin] ?? []
    log.push({ steps: steps.length, pose, stopped })
    this.parkingLog[vin] = log
    return { pose, stopped }
  }

  /**
   * Stops the car and emits `assist.request` (M6.3). The clip comes from the attached chase
   * camera when there is one; the offline AI answers from the truth hints otherwise.
   * @param vin - Car VIN.
   * @param case_id - Case id, e.g. 'AC-VIN-1-1'.
   * @param reason - Obstacle description.
   * @param options - Offered options.
   */
  raiseAssist(vin: string, case_id: string, reason: string, options: AssistOption[]): void {
    const car = this.cars.get(vin)
    const frames = [...this.assistFrames(vin)]
    if (car) {
      car.stoppedForAssist = true
      car.assistPending = true
      car.speed_mps = 0
    }
    this.assists.set(case_id, { vin, options: [...options], frames })
    this.emit({
      event: 'assist.request',
      case_id,
      vin,
      location: car?.location ?? '',
      reason,
      options: [...options],
      clip_frames: [...frames],
    })
  }

  /**
   * Spawns a prop (M6.1).
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
   * Stores an artifact in IndexedDB (M10.7), falling back to memory without IndexedDB.
   * @param uri - Artifact URI.
   * @param blob - Content.
   */
  async storeArtifact(uri: string, blob: Blob): Promise<void> {
    if (typeof indexedDB === 'undefined') {
      this.artifacts.set(uri, blob)
      return
    }
    try {
      await putArtifact(uri, blob)
    } catch {
      this.artifacts.set(uri, blob)
    }
  }

  // --- Simulation -----------------------------------------------------------------------------

  /**
   * Advances the simulation in fixed 0.1 s steps and emits status at 1 Hz.
   * @param dt - Sim seconds.
   */
  tick(dt: number): void {
    if (!(dt > 0)) return
    this.clock += dt
    let left = dt
    while (left > 1e-9) {
      const step = Math.min(FIXED_STEP_S, left)
      left -= step
      for (const car of [...this.cars.values()]) this.tickCar(car, step)
    }
    this.statusClock += dt
    while (this.statusClock + 1e-9 >= STATUS_PERIOD_S) {
      this.statusClock -= STATUS_PERIOD_S
      this.emitStatuses()
    }
  }

  private point(name: string): NamedPoint {
    if (!isPointName(name)) throw new Error(`Unknown map point: ${name}`)
    const point = this.graph.points[name]
    if (!point) throw new Error(`Unknown map point: ${name}`)
    return point
  }

  private startMission(car: EngineCar, cmd: MissionGoCmd): void {
    const waypoints = cmd.waypoints.filter((name): name is PointName => isPointName(name))
    if (waypoints.length === 0) return
    const destination = this.point(waypoints[waypoints.length - 1])
    const fromPull = this.pullOverAt(car.x, car.y)

    // Slot-to-slot moves (M4.4): drive directly when the target is near enough.
    if (fromPull && waypoints.length === 1 && this.pullOverAt(destination.x, destination.y)) {
      const yaw = (fromPull.yaw * Math.PI) / 180
      const ahead =
        (destination.x - car.x) * Math.cos(yaw) + (destination.y - car.y) * Math.sin(yaw)
      if (ahead > 0 && ahead <= SLOT_MOVE_FORWARD_LIMIT_M) {
        this.assign(car, this.directMission(car, cmd, waypoints, destination, false))
        return
      }
      if (ahead < 0 && -ahead <= SLOT_MOVE_REVERSE_LIMIT_M) {
        this.assign(car, this.directMission(car, cmd, waypoints, destination, true))
        return
      }
    }

    // Slot departure (M4.3): a car leaving a pull-over starts from the lane 5 m ahead.
    if (fromPull) {
      const yaw = (fromPull.yaw * Math.PI) / 180
      car.x = fromPull.x + Math.cos(yaw) * PULL_OUT_M
      car.y = fromPull.y + Math.sin(yaw) * PULL_OUT_M
      car.yaw = yaw
    }

    const start = this.nearestPoint(car.x, car.y)
    const names: PointName[] = [start]
    let cursor: PointName = start
    for (const waypoint of waypoints) {
      names.push(...this.graph.route(cursor, waypoint).slice(1))
      cursor = waypoint
    }
    const route: PathPoint[] = []
    for (let index = 0; index < names.length - 1; index++) {
      const samples = sampleCenterline(this.point(names[index]), this.point(names[index + 1]))
      for (let sample = index === 0 ? 0 : 1; sample < samples.length; sample++) {
        route.push(samples[sample])
      }
    }
    if (route.length === 0) route.push({ ...destination })
    this.assign(car, {
      cmd,
      waypoints,
      waypointIndex: 0,
      route,
      routeIndex: 0,
      precise: PRECISE_WAYPOINTS.has(waypoints[waypoints.length - 1]),
      reverse: false,
      gateRecorded: false,
      preciseElapsed: 0,
      travelled_m: 0,
      total_m: routeLength(route),
      routine: null,
    })
  }

  private directMission(
    car: EngineCar,
    cmd: MissionGoCmd,
    waypoints: PointName[],
    destination: NamedPoint,
    reverse: boolean,
  ): ActiveMission {
    return {
      cmd,
      waypoints,
      waypointIndex: 0,
      route: [
        { x: car.x, y: car.y },
        { x: destination.x, y: destination.y },
      ],
      routeIndex: 0,
      precise: true,
      reverse,
      gateRecorded: false,
      preciseElapsed: 0,
      travelled_m: 0,
      total_m: Math.hypot(destination.x - car.x, destination.y - car.y),
      routine: null,
    }
  }

  private assign(car: EngineCar, mission: ActiveMission): void {
    car.mission = mission
    car.held = false
    car.holdLeft = null
  }

  private nearestPoint(x: number, y: number): PointName {
    let best = Object.keys(this.graph.points)[0] as PointName
    let bestDistance = Number.POSITIVE_INFINITY
    for (const [name, point] of Object.entries(this.graph.points)) {
      const distance = (point.x - x) ** 2 + (point.y - y) ** 2
      if (distance < bestDistance) {
        bestDistance = distance
        best = name as PointName
      }
    }
    return best
  }

  private tickCar(car: EngineCar, dt: number): void {
    const mission = car.mission
    if (!mission) {
      car.speed_mps = 0
      return
    }
    if (car.held) {
      if (car.holdLeft !== null) {
        car.holdLeft -= dt
        if (car.holdLeft <= 0) {
          car.held = false
          car.holdLeft = null
        }
      }
      car.speed_mps = 0
      return
    }
    if (car.stoppedForAssist) {
      car.speed_mps = 0
      return
    }
    if (mission.routine) {
      this.tickRoutine(car, mission, dt)
      return
    }

    // Waiting for removal (M6.4): hold until the corridor clears, then record the wait.
    if (car.waitingCase) {
      car.speed_mps = 0
      if (this.nearestProp(car, CLEAR_RADIUS_M, BLOCKED_LATERAL_M) !== null) {
        car.waited_s += dt
        return
      }
      const caseId = car.waitingCase
      const outcome = this.assistOutcomes[caseId] as Record<string, unknown> | undefined
      this.assistOutcomes[caseId] = { ...outcome, waited_s: Math.round(car.waited_s * 10) / 10 }
      car.waitingCase = null
      return
    }

    // Pass-left backup (M6.4): back up for room before swerving around the obstacle.
    if (car.avoidPhase === 'backup') {
      car.speed_mps = AVOID_BACKUP_SPEED_MPS
      car.x -= Math.cos(car.yaw) * AVOID_BACKUP_SPEED_MPS * dt
      car.y -= Math.sin(car.yaw) * AVOID_BACKUP_SPEED_MPS * dt
      const backed = Math.hypot(
        car.x - (car.avoidStart?.x ?? car.x),
        car.y - (car.avoidStart?.y ?? car.y),
      )
      if (backed >= AVOID_BACKUP_M) car.avoidPhase = 'swerve'
      return
    }

    mission.routeIndex = nearestIndex(mission.route, car.x, car.y, mission.routeIndex)
    const last = this.point(mission.waypoints[mission.waypoints.length - 1])
    const distanceToEnd = Math.hypot(last.x - car.x, last.y - car.y)

    // Intermediate waypoints are passed through; the last one stops the car.
    while (mission.waypointIndex < mission.waypoints.length - 1) {
      const waypoint = this.point(mission.waypoints[mission.waypointIndex])
      if (Math.hypot(waypoint.x - car.x, waypoint.y - car.y) > ARRIVAL_RADIUS_M + 1) break
      car.location = mission.waypoints[mission.waypointIndex]
      mission.waypointIndex++
    }

    // Slot-to-slot reverse (M4.4): back straight towards the target, then square up.
    if (mission.reverse) {
      const step = Math.min(REVERSE_SPEED_MPS * dt, Math.max(0, distanceToEnd - SQUARE_UP_RADIUS_M))
      car.x -= Math.cos(car.yaw) * step
      car.y -= Math.sin(car.yaw) * step
      car.speed_mps = dt > 0 ? step / dt : 0
      mission.travelled_m += step
      if (distanceToEnd - step <= SQUARE_UP_RADIUS_M + 1e-9) {
        this.squareUp(car, last)
        this.finish(car, mission)
      }
      return
    }

    let targetSpeed = (mission.cmd.max_speed_kmh ?? DEFAULT_SPEED_KMH) / 3.6
    if (mission.waypoints.includes('inspection_gate')) {
      const gate = this.point('inspection_gate')
      const toGate = Math.hypot(gate.x - car.x, gate.y - car.y)
      if (toGate <= GATE_SLOW_ZONE_M) targetSpeed = Math.min(targetSpeed, GATE_SPEED_MPS)
      if (toGate <= GATE_RECORD_RADIUS_M && !mission.gateRecorded) {
        mission.gateRecorded = true
        this.passes[car.vin] = car.speed_mps * 3.6
      }
    }

    // Pull-over (M4.2): walking pace inside the run distance, square up at the radius.
    if (mission.precise) {
      if (distanceToEnd <= SQUARE_UP_RADIUS_M) {
        this.squareUp(car, last)
        this.arrive(car, mission)
        return
      }
      if (distanceToEnd <= PULL_OVER_RUN_M) {
        mission.preciseElapsed += dt
        targetSpeed = Math.min(targetSpeed, PULL_OVER_SPEED_MPS)
        if (mission.preciseElapsed >= PRECISE_TIMEOUT_S) {
          this.squareUp(car, last)
          this.arrive(car, mission)
          return
        }
      }
    } else if (distanceToEnd <= ARRIVAL_RADIUS_M) {
      this.arrive(car, mission)
      return
    }

    // Queuing (M4.1): hold the standing gap plus a rolling headway behind lane traffic. Queuing
    // never raises an assist request; only raiseAssist does.
    const leader = this.leaderGap(car)
    if (leader !== null) {
      const required = QUEUE_GAP_M + car.speed_mps * QUEUE_HEADWAY_S
      targetSpeed = Math.min(targetSpeed, Math.max(0, (leader - required) / dt))
    }

    // Obstacle scan (M6.2): brake for props and workers inside the lookahead envelope. While a
    // pass-left runs the offset lane keeps the clearance, so the scan does not apply.
    if (car.avoidPhase === null) {
      const scanRange = OBSTACLE_BASE_M + (car.speed_mps * car.speed_mps) / (2 * PROP_DECEL_MPS2)
      const obstacle = this.nearestProp(car, scanRange, OBSTACLE_LATERAL_M)
      if (obstacle !== null) {
        targetSpeed = Math.min(
          targetSpeed,
          Math.sqrt(2 * PROP_DECEL_MPS2 * Math.max(0, obstacle.ahead - PROP_STOP_GAP_M)),
        )
      }
    }

    // Blocked detection (M6.3): still for 5 s with a non-plant obstacle close ahead.
    const near = this.nearestProp(car, BLOCKED_RADIUS_M, BLOCKED_LATERAL_M)
    if (car.speed_mps < BLOCKED_SPEED_MPS && near !== null && car.avoidPhase === null) {
      car.blockedFor += dt
    } else {
      car.blockedFor = 0
    }
    if (car.blockedFor >= BLOCKED_TIME_S && !car.assistPending && !car.waitingCase) {
      this.raiseBlockedAssist(car, near?.kind ?? 'box')
      return
    }

    // Pass-left swerve (M6.4): crawl with a lateral offset until the obstacle is well behind.
    if (car.avoidPhase === 'swerve') {
      targetSpeed = Math.min(targetSpeed, AVOID_SPEED_MPS)
      const closest = this.nearestPropDistance(car)
      if (closest !== null) car.avoidClosest = Math.min(car.avoidClosest, closest)
      // Keep the offset until the obstacle is well behind: clearing while it is still abeam
      // would merge the car straight back into it.
      const tracked = this.nearestProp(car, AVOID_TRACK_RANGE_M, AVOID_TRACK_LATERAL_M, true)
      if (tracked === null || tracked.ahead < -0.5) {
        car.avoidPhase = null
        if (car.avoidCase) {
          const outcome = this.assistOutcomes[car.avoidCase] as Record<string, unknown> | undefined
          this.assistOutcomes[car.avoidCase] = {
            ...outcome,
            closest_m: Math.round(car.avoidClosest * 100) / 100,
            contact: false,
          }
          car.avoidCase = null
        }
      }
    }

    const stopRadius = mission.precise ? SQUARE_UP_RADIUS_M : ARRIVAL_RADIUS_M
    const braking = Math.sqrt(2 * BRAKE_MPS2 * Math.max(0, distanceToEnd - stopRadius))
    const speed = approachSpeed(car.speed_mps, Math.min(targetSpeed, braking), dt)
    let lookahead = pointAhead(mission.route, mission.routeIndex, LOOKAHEAD_M)
    if (mission.precise && distanceToEnd <= PULL_OVER_RUN_M) {
      // Aim 4 m beyond the pull-over along its heading, so the approach lines up with the slot.
      const pullYaw = (last.yaw * Math.PI) / 180
      lookahead = {
        x: last.x + Math.cos(pullYaw) * PULL_OVER_AIM_M,
        y: last.y + Math.sin(pullYaw) * PULL_OVER_AIM_M,
      }
    }
    if (car.avoidPhase === 'swerve') {
      // Pass on the left of the lane (heading rotated +90 degrees in the map frame).
      lookahead = {
        x: lookahead.x - Math.sin(car.yaw) * AVOID_OFFSET_M,
        y: lookahead.y + Math.cos(car.yaw) * AVOID_OFFSET_M,
      }
    }
    const steer = purePursuitSteer(car, lookahead)
    const next = stepVehicle(car, steer, speed, dt)
    mission.travelled_m += Math.hypot(next.x - car.x, next.y - car.y)
    car.x = next.x
    car.y = next.y
    car.yaw = next.yaw
    car.speed_mps = next.speed_mps
  }

  /** Pull-over point within the parked radius of a pose, or null. */
  private pullOverAt(x: number, y: number): NamedPoint | null {
    for (const point of this.pullOvers) {
      if (Math.hypot(point.x - x, point.y - y) <= PULLOVER_RADIUS_M) return point
    }
    return null
  }

  /**
   * Nearest prop inside a range and lateral corridor (M6.2/M6.3). With `bidirectional` the
   * corridor extends behind the car too (used to keep a pass-left active until it is past).
   * @param car - Scanning car.
   * @param range - Lookahead distance, metres.
   * @param lateral - Half-width of the corridor, metres.
   * @param bidirectional - Count props behind the car as well.
   * @returns Kind, ahead distance and straight distance, or null.
   */
  private nearestProp(
    car: EngineCar,
    range: number,
    lateral: number,
    bidirectional = false,
  ): { kind: PropKind; ahead: number; distance: number } | null {
    const fx = Math.cos(car.yaw)
    const fy = Math.sin(car.yaw)
    let best: { kind: PropKind; ahead: number; distance: number } | null = null
    for (const prop of this.props.values()) {
      const dx = prop.x - car.x
      const dy = prop.y - car.y
      const ahead = dx * fx + dy * fy
      const reach = bidirectional ? Math.abs(ahead) : ahead
      if (reach > range) continue
      if (!bidirectional && ahead <= 0) continue
      if (Math.abs(dx * fy - dy * fx) > lateral) continue
      const distance = Math.hypot(dx, dy)
      if (best === null || reach < Math.abs(best.ahead)) best = { kind: prop.kind, ahead, distance }
    }
    return best
  }

  /** Straight distance to the nearest prop of any kind, or null. */
  private nearestPropDistance(car: EngineCar): number | null {
    let nearest: number | null = null
    for (const prop of this.props.values()) {
      const distance = Math.hypot(prop.x - car.x, prop.y - car.y)
      if (nearest === null || distance < nearest) nearest = distance
    }
    return nearest
  }

  /**
   * Raises the assist case for a blocked car (M6.3): a worker always offers waiting; anything
   * else also offers passing left or the Lane C reroute.
   * @param car - Blocked car.
   * @param kind - Blocking prop kind.
   */
  private raiseBlockedAssist(car: EngineCar, kind: PropKind): void {
    const options: AssistOption[] =
      kind === 'worker'
        ? ['wait_for_removal']
        : ['wait_for_removal', 'pass_left_3kmh', 'reroute_lane_c']
    car.assistSeq += 1
    this.raiseAssist(
      car.vin,
      `AC-${car.vin}-${car.assistSeq}`,
      `${kind} blocking the lane ahead`,
      options,
    )
  }

  /**
   * Replaces the mission route with one that runs through Lane C (M6.4).
   * @param car - Car whose assist chose the reroute.
   * @returns True when a Lane C route was built.
   */
  private rerouteViaLaneC(car: EngineCar): boolean {
    const mission = car.mission
    if (!mission) return false
    const destination = mission.waypoints[mission.waypoints.length - 1]
    try {
      const start = this.nearestPoint(car.x, car.y)
      const names: PointName[] = [start, ...this.graph.route(start, 'lane_c').slice(1)]
      names.push(...this.graph.route('lane_c', destination).slice(1))
      const route: PathPoint[] = []
      for (let index = 0; index < names.length - 1; index++) {
        const samples = sampleCenterline(this.point(names[index]), this.point(names[index + 1]))
        for (let sample = index === 0 ? 0 : 1; sample < samples.length; sample++) {
          route.push(samples[sample])
        }
      }
      if (route.length === 0) return false
      mission.route = route
      mission.routeIndex = 0
      mission.travelled_m = 0
      mission.total_m = routeLength(route)
      return true
    } catch {
      return false
    }
  }

  /**
   * Gap to the nearest car ahead that holds the queue, or null when the lane is clear. A car
   * holds the queue while it has an active mission or stands still in the lane; cars parked on
   * pull-over points are ignored (TEAM_PLAN M4.1 shoulder tolerance).
   */
  private leaderGap(car: EngineCar): number | null {
    const fx = Math.cos(car.yaw)
    const fy = Math.sin(car.yaw)
    let nearest: number | null = null
    for (const other of this.cars.values()) {
      if (other.vin === car.vin) continue
      if (!other.mission && (other.speed_mps !== 0 || this.pullOverAt(other.x, other.y))) continue
      const dx = other.x - car.x
      const dy = other.y - car.y
      const ahead = dx * fx + dy * fy
      if (ahead <= 0 || ahead > QUEUE_LOOKAHEAD_M) continue
      if (Math.abs(dx * fy - dy * fx) > QUEUE_LATERAL_M) continue
      if (nearest === null || ahead < nearest) nearest = ahead
    }
    return nearest
  }

  /**
   * Snaps the car onto the exact point pose (TEAM_PLAN M4.2 square-up, zero yaw error).
   * @param car - Car to place.
   * @param point - Named point whose pose is taken.
   */
  private squareUp(car: EngineCar, point: NamedPoint): void {
    car.x = point.x
    car.y = point.y
    car.yaw = (point.yaw * Math.PI) / 180
    car.speed_mps = 0
  }

  /**
   * Obstacles within {@link PARKING_OBSTACLE_RANGE_M} of a point (M7.2): vehicles and props.
   * @param vin - Car to exclude.
   * @param x - Centre x, CARLA frame.
   * @param y - Centre y, CARLA frame.
   * @returns Obstacle boxes.
   */
  private nearbyObstacles(vin: string, x: number, y: number): ObstacleBox[] {
    const near = (px: number, py: number): boolean =>
      Math.hypot(px - x, py - y) <= PARKING_OBSTACLE_RANGE_M
    const boxes: ObstacleBox[] = []
    for (const car of this.cars.values()) {
      if (car.vin === vin || !near(car.x, car.y)) continue
      boxes.push({
        id: car.vin,
        x: car.x,
        y: car.y,
        yaw: (car.yaw * 180) / Math.PI,
        length: CAR_LENGTH_M,
        width: CAR_WIDTH_M,
        moving: car.speed_mps > 0,
      })
    }
    for (const prop of this.props.values()) {
      if (!near(prop.x, prop.y)) continue
      const footprint = PROP_FOOTPRINTS[prop.kind]
      if (footprint.height < FLAT_PROP_HEIGHT_M) continue
      boxes.push({
        id: prop.id,
        x: prop.x,
        y: prop.y,
        yaw: prop.yaw,
        length: footprint.length,
        width: footprint.width,
        moving: footprint.moving,
      })
    }
    return boxes
  }

  /**
   * Predictive safety stop (M7.3): probes {@link SAFETY_PROBE_M} ahead along the step's arc and
   * halts when any obstacle clearance falls below {@link SAFETY_CLEARANCE_M}.
   * @param car - Car about to step.
   * @param step - Control step.
   * @param obstacles - Nearby obstacle boxes.
   * @returns True when the step must not run.
   */
  private safetyStop(car: EngineCar, step: ControlStep, obstacles: ObstacleBox[]): boolean {
    const speed = Math.max(1e-6, step.speed_mps)
    const fraction = Math.min(1, SAFETY_PROBE_M / (speed * Math.max(1e-6, step.duration_s)))
    const probe = controlStepDelta(car, { ...step, duration_s: step.duration_s * fraction })
    const px = car.x + probe.dx
    const py = car.y + probe.dy
    return obstacles.some((box) => distanceToBox(px, py, box) < SAFETY_CLEARANCE_M)
  }

  /**
   * Final waypoint reached: start the R-12 routine when the mission asks for one, else finish.
   * @param car - Car that arrived.
   * @param mission - Its mission.
   */
  private arrive(car: EngineCar, mission: ActiveMission): void {
    car.speed_mps = 0
    if (mission.cmd.routine) {
      const steps = r12Steps(mission.cmd.routine_steps)
      if (steps.length > 0) {
        mission.routine = {
          id: mission.cmd.routine,
          steps,
          index: 0,
          elapsed: 0,
          startPose: { x: car.x, y: car.y },
          brakingFrom: null,
        }
        return
      }
    }
    this.finish(car, mission)
  }

  /**
   * Runs the R-12 sequence: standing steps on timers, a real 3 m reverse and a real 20 km/h
   * brake test; lamp state follows the step (indicators blink). Each finished step emits
   * `track.step`; the last one finishes the mission. Telemetry stays nominal under faults (M5.2).
   */
  private tickRoutine(car: EngineCar, mission: ActiveMission, dt: number): void {
    const routine = mission.routine
    if (!routine) return
    const spec = routine.steps[routine.index]
    if (!spec) {
      car.lamps = offLamps()
      this.finish(car, mission)
      return
    }
    routine.elapsed += dt
    const telemetry: Record<string, unknown> = { ...spec.telemetry }
    let done = false

    if (spec.name === 'reverse') {
      const travelled = Math.hypot(car.x - routine.startPose.x, car.y - routine.startPose.y)
      const step = Math.min(REVERSE_SPEED_MPS * dt, Math.max(0, R12_REVERSE_M - travelled))
      car.x -= Math.cos(car.yaw) * step
      car.y -= Math.sin(car.yaw) * step
      car.speed_mps = dt > 0 ? step / dt : 0
      const total = travelled + step
      telemetry.distance_m = Math.round(total * 100) / 100
      telemetry.lateral_m = 0
      done = total >= R12_REVERSE_M - 1e-9 || routine.elapsed >= spec.duration_s
    } else if (spec.name === 'brake_test') {
      const peak = R12_BRAKE_TEST_KMH / 3.6
      if (routine.brakingFrom === null && car.speed_mps < peak) {
        car.speed_mps = Math.min(peak, car.speed_mps + MAX_ACCEL_MPS2 * dt)
      } else {
        if (routine.brakingFrom === null) {
          routine.brakingFrom = car.speed_mps
          routine.startPose = { x: car.x, y: car.y }
        }
        car.speed_mps = Math.max(0, car.speed_mps - BRAKE_MPS2 * dt)
      }
      car.x += Math.cos(car.yaw) * car.speed_mps * dt
      car.y += Math.sin(car.yaw) * car.speed_mps * dt
      if (routine.brakingFrom !== null) {
        telemetry.start_speed_kmh = Math.round(routine.brakingFrom * 3.6 * 10) / 10
        telemetry.stopping_distance_m =
          Math.round(Math.hypot(car.x - routine.startPose.x, car.y - routine.startPose.y) * 100) /
          100
      }
      done = routine.brakingFrom !== null && car.speed_mps <= 0
    } else {
      car.speed_mps = 0
      done = routine.elapsed >= spec.duration_s
    }

    if (spec.name === 'indicators') {
      const blink = Math.floor(routine.elapsed / R12_BLINK_PERIOD_S) % 2 === 0
      car.lamps = { ...offLamps(), indicator_left: blink, indicator_right: !blink }
    } else {
      car.lamps = { ...spec.lamps }
    }

    if (!done) return
    this.emit({
      event: 'track.step',
      vin: car.vin,
      mission_id: mission.cmd.mission_id,
      routine: routine.id,
      step: spec.step,
      name: spec.name,
      telemetry,
    })
    routine.index++
    routine.elapsed = 0
    routine.brakingFrom = null
    if (routine.index >= routine.steps.length) {
      car.lamps = offLamps()
      car.speed_mps = 0
      this.finish(car, mission)
    }
  }

  private finish(car: EngineCar, mission: ActiveMission): void {
    const location = mission.waypoints[mission.waypoints.length - 1]
    car.location = location
    car.mission = null
    car.speed_mps = 0
    this.emit({
      event: 'mission.done',
      mission_id: mission.cmd.mission_id,
      vin: car.vin,
      location,
    })
  }

  private emitStatuses(): void {
    for (const car of this.cars.values()) {
      const mission = car.mission
      const total = mission?.total_m ?? 0
      this.emit({
        event: 'status',
        vin: car.vin,
        location: car.location,
        x: car.x,
        y: car.y,
        speed_kmh: car.speed_mps * 3.6,
        mission_id: mission?.cmd.mission_id ?? '',
        progress: mission && total > 0 ? Math.min(1, mission.travelled_m / total) : 1,
        held: car.held,
        stopped_for_assist: car.stoppedForAssist,
      })
    }
  }

  private emit(event: SimEvent): void {
    for (const cb of [...(this.listeners.get(event.event) ?? [])]) cb(event)
  }
}
