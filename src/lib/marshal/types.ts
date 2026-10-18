// The Contract (TEAM_PLAN "The Contract"). Every message between the Marshal orchestrator and
// the driving simulator crosses this boundary. Both sides depend only on this file.
// Pure types: no runtime code, no imports.

// ---------------------------------------------------------------------------------------------
// Orchestrator -> Simulator (Commands)
// ---------------------------------------------------------------------------------------------

export type MissionGoCmd = {
  op: 'mission.go'
  mission_id: string
  vin: string
  /** Named map points (see src/lib/marshal/points.ts). */
  waypoints: string[]
  /** Default 10. */
  max_speed_kmh?: number
  /** e.g. "R-12". */
  routine?: string
  /** Subset of steps, e.g. [1]. */
  routine_steps?: number[]
}

export type MissionHoldCmd = {
  op: 'mission.hold'
  vin: string
  duration_s?: number
  reason: string
}

export type MissionResumeCmd = { op: 'mission.resume'; vin: string }

export type AssistChooseCmd = {
  op: 'assist.choose'
  case_id: string
  vin: string
  option: 'wait_for_removal' | 'pass_left_3kmh' | 'reroute_lane_c'
  chosen_by: 'supervisor' | 'gemini'
}

export type SimCommand = MissionGoCmd | MissionHoldCmd | MissionResumeCmd | AssistChooseCmd

// ---------------------------------------------------------------------------------------------
// Simulator -> Orchestrator (Events)
// ---------------------------------------------------------------------------------------------

export type StatusEvent = {
  event: 'status'
  vin: string
  /** Last named map point passed. */
  location: string
  x: number
  y: number
  speed_kmh: number
  mission_id: string
  /** 0-1. */
  progress: number
  held: boolean
  stopped_for_assist: boolean
}

export type MissionDoneEvent = {
  event: 'mission.done'
  mission_id: string
  vin: string
  location: string
}

export type TrackStepEvent = {
  event: 'track.step'
  vin: string
  mission_id: string
  routine: string
  step: number
  name: string
  telemetry: Record<string, unknown>
}

export type AssistRequestEvent = {
  event: 'assist.request'
  case_id: string
  vin: string
  location: string
  reason: string
  options: AssistOption[]
  /** base64 JPEG[], 8 frames. */
  clip_frames: string[]
}

export type SimEvent = StatusEvent | MissionDoneEvent | TrackStepEvent | AssistRequestEvent

export type AssistOption = 'wait_for_removal' | 'pass_left_3kmh' | 'reroute_lane_c'

// ---------------------------------------------------------------------------------------------
// DrivingSystem interface (the engine must implement this)
// ---------------------------------------------------------------------------------------------

export interface DrivingSystem {
  // Commands
  send(cmd: SimCommand): void

  // Events
  on(event: 'status', cb: (e: StatusEvent) => void): void
  on(event: 'mission.done', cb: (e: MissionDoneEvent) => void): void
  on(event: 'track.step', cb: (e: TrackStepEvent) => void): void
  on(event: 'assist.request', cb: (e: AssistRequestEvent) => void): void

  // Direct engine calls (flows + dashboard)
  spawn(
    vin: string,
    opts: { at: string; colour: [number, number, number]; behind_m?: number },
  ): void
  despawn(vin: string): void
  setFault(vin: string, faults: Set<'headlights' | 'brake_lights' | 'indicators'>): void
  repair(vin: string, defects: string[]): void
  getPositionXY(vin: string): { x: number; y: number; yaw: number }
  getTransform(point: string): { x: number; y: number; yaw: number }
  /** base64 JPEG[]. */
  getAssistMedia(case_id: string): string[]
  raiseAssist(vin: string, case_id: string, reason: string, options: AssistOption[]): void
  spawnProp(
    id: string,
    kind: 'box' | 'worker' | 'cone' | 'barrel' | 'truck',
    x: number,
    y: number,
    yaw: number,
  ): void
  removeProp(id: string): void
  storeArtifact(uri: string, blob: Blob): Promise<void>

  // State records (read-only from orchestrator)
  /** vin -> km/h at gate. */
  readonly passes: Record<string, number>
  /** case_id -> outcome. */
  readonly assistOutcomes: Record<string, unknown>
  /** vin -> session log. */
  readonly parkingLog: Record<string, unknown[]>
}

// ---------------------------------------------------------------------------------------------
// Qais-owned extensions used by the flows and server functions.
// Coordinates are in the CARLA map frame (metres); yaw is in degrees, like the named points.
// ---------------------------------------------------------------------------------------------

/** The four fixed gate cameras around `inspection_gate` (TEAM_PLAN M3.1). */
export type GateView = 'front' | 'rear' | 'left' | 'right'

/** One base64 JPEG per gate camera, no overlays. */
export type GateFrames = Record<GateView, string>

/** Gate camera capture port, implemented by the simulator's GateCamera (M3.2). */
export interface GateCapturePort {
  /**
   * Captures the requested gate views for a car.
   * @param vin - Car being inspected.
   * @param views - Views to render.
   * @returns base64 JPEG per view.
   */
  capture(vin: string, views: GateView[]): Promise<GateFrames>
}

/** On-board evidence camera port (M5.1): rolling buffer of the last 8 frames per camera. */
export interface EvidencePort {
  /**
   * Returns the evidence buffer for a car.
   * @param vin - Car on the test track.
   * @param camera - Evidence camera.
   * @returns base64 JPEG[], oldest first.
   */
  getEvidenceFrames(vin: string, camera: 'front' | 'rear'): string[]
}

/** Car pose: centre of the car, yaw in degrees. */
export type Pose = { x: number; y: number; yaw: number }

/** Obstacle footprint reported per tick by the simulator (M7.2). */
export type ObstacleBox = {
  id: string
  x: number
  y: number
  /** Degrees. */
  yaw: number
  length: number
  width: number
  moving: boolean
}

/** Target parking slot: centre pose of a parked car (e.g. `yard.Y7`). */
export type ParkingSlot = { id: string; x: number; y: number; yaw: number }

/** One kinematic bicycle-model control segment (M7.1). */
export type ControlStep = {
  /** Speed magnitude in m/s; direction comes from `gear`. */
  speed_mps: number
  /** -1..1, maps to +/-30 deg front-wheel angle; positive = left in slot frame. */
  wheel: number
  duration_s: number
  gear: 'D' | 'R'
}

/** Parking execution port, implemented by the simulator's bicycle-model control loop. */
export interface ParkingPort {
  /**
   * Lists obstacles within 15m of the slot.
   * @param vin - Car being parked (excluded from the result).
   * @param slot - Target slot.
   * @returns Obstacle boxes in the map frame.
   */
  getObstacles(vin: string, slot: ParkingSlot): ObstacleBox[]
  /**
   * Executes control steps with the M7.3 safety stop.
   * @param vin - Car being parked.
   * @param steps - Control sequence to run.
   * @returns Final pose and whether the safety stop fired.
   */
  execute(vin: string, steps: ControlStep[]): Promise<{ pose: Pose; stopped: boolean }>
}

/** Gate check outcome (M3.3). */
export type GateResult = {
  pass: boolean
  colour: string
  confidence: number
  faults: string[]
  degraded: boolean
  /** True when the input failed schema validation and the answer came from the offline logic. */
  invalidInput?: boolean
}

/** Lamp check outcome (M5.5): `expected` from telemetry, `observed` from the camera. */
export type LampResult = {
  expected: string
  observed: string
  pass: boolean
  degraded: boolean
  /** True when the input failed schema validation and the answer came from the offline logic. */
  invalidInput?: boolean
}

/** Normalised (0-1) image-space box for the dashboard overlay. */
export type BoundingBox = { label: string; x: number; y: number; w: number; h: number }

/** Assist clip analysis (M6.6). */
export type AssistAnalysis = {
  description: string
  boxes: BoundingBox[]
  recommendation: AssistOption
  degraded: boolean
  /** True when the input failed schema validation and the answer came from the offline logic. */
  invalidInput?: boolean
}

/** Parking decision (M7.5). */
export type ParkingDecision = 'path_a' | 'path_b' | 'path_c' | 'parked' | 'ask_person'

/** One planning step: the chosen control sequence (empty for parked/ask_person). */
export type ParkingStep = {
  path: ControlStep[]
  decision: ParkingDecision
  confidence: number
  degraded: boolean
  /** True when the input failed schema validation and the answer came from the offline logic. */
  invalidInput?: boolean
}

/** Route options at `report_point` (M9.1). */
export type RouteOption = 'finishing_corner' | 'lane_c'

export type RouteChoice = {
  route: RouteOption
  confidence: number
  reason: string
  degraded: boolean
  /** True when the input failed schema validation and the answer came from the offline logic. */
  invalidInput?: boolean
}

/** One slot-to-slot move (M9.2). */
export type YardMove = { vin: string; from: string; to: string }

export type YardReplan = {
  moves: YardMove[]
  reason: string
  degraded: boolean
  /** True when the input failed schema validation and the answer came from the offline logic. */
  invalidInput?: boolean
}
