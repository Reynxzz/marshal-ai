// Per-car kinematics: moves along a precomputed path of world points at a fixed speed.
// No physics, no three.js — deterministic linear motion between points.

import type { SimEvent } from './events'
import { type RoutineStep, routineFor } from './routines'
import type { Point, World } from './world'

export type VehicleState = 'idle' | 'moving' | 'stopped' | 'held' | 'done'

export type Path = { points: Point[]; cumulative: number[]; total: number }

export function buildPath(names: string[], world: World): Path {
  const points = names.map((n) => world.point(n))
  const cumulative: number[] = [0]
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1] as Point
    const b = points[i] as Point
    cumulative.push((cumulative[i - 1] as number) + Math.hypot(b.x - a.x, b.y - a.y))
  }
  return { points, cumulative, total: cumulative[cumulative.length - 1] ?? 0 }
}

/** Position and heading at a signed distance along the path (negative extrapolates backwards). */
export function positionAt(path: Path, distance: number): { x: number; y: number; yaw: number } {
  const pts = path.points
  if (pts.length === 0) return { x: 0, y: 0, yaw: 0 }
  if (pts.length === 1) return { x: pts[0].x, y: pts[0].y, yaw: 0 }

  if (distance <= 0) {
    const o = pts[0] as Point
    const d = pts[1] as Point
    const len = Math.hypot(d.x - o.x, d.y - o.y) || 1
    const ux = (d.x - o.x) / len
    const uy = (d.y - o.y) / len
    return { x: o.x + ux * distance, y: o.y + uy * distance, yaw: Math.atan2(uy, ux) }
  }
  if (distance >= path.total) {
    const o = pts[pts.length - 2] as Point
    const d = pts[pts.length - 1] as Point
    return { x: d.x, y: d.y, yaw: Math.atan2(d.y - o.y, d.x - o.x) }
  }
  let i = 0
  while (i < pts.length - 2 && distance > (path.cumulative[i + 1] as number)) i++
  const o = pts[i] as Point
  const d = pts[i + 1] as Point
  const segStart = path.cumulative[i] as number
  const segLen = (path.cumulative[i + 1] as number) - segStart || 1
  const t = (distance - segStart) / segLen
  return {
    x: o.x + (d.x - o.x) * t,
    y: o.y + (d.y - o.y) * t,
    yaw: Math.atan2(d.y - o.y, d.x - o.x),
  }
}

export type CarSnapshot = {
  vin: string
  x: number
  y: number
  yaw: number
  speedKmh: number
  state: VehicleState
  colour: string
  lamps: { headlights: boolean; brake_lights: boolean }
  fault?: string
}

export type MissionSpec = {
  id: string
  waypoints: string[]
  routine?: string
  routineSteps?: string[]
  speedKmh?: number
}

function firstOutgoing(world: World, at: string): string {
  const seg = world.segments.find((s) => s.from === at)
  return seg ? seg.to : at
}

export class Vehicle {
  readonly vin: string
  private readonly world: World
  path: Path
  distance: number
  speedKmh: number
  state: VehicleState = 'idle'
  reachedIndex = 0
  location: string
  colour: string
  faultStep?: string

  private normalHeadlights = true
  private normalBrake = false

  mission?: { id: string; status: 'queued' | 'running' | 'done' | 'held' }
  private routine: RoutineStep[] = []
  private routineActive = false
  private routineIndex = 0
  private routineTimer = 0
  private holdUntil?: number

  constructor(
    vin: string,
    world: World,
    at: string,
    opts: { colour?: string; queueOffsetM?: number; speedKmh?: number } = {},
  ) {
    this.vin = vin
    this.world = world
    this.location = at
    this.colour = opts.colour ?? '#cccccc'
    this.speedKmh = opts.speedKmh ?? 20
    const neighbor = firstOutgoing(world, at)
    this.path = buildPath(neighbor === at ? [at] : [at, neighbor], world)
    this.distance = -(opts.queueOffsetM ?? 0)
  }

  /** Dispatch a mission starting from the car's current location. Keeps a queue offset. */
  dispatch(mission: MissionSpec): void {
    const names: string[] = []
    let from = this.location
    for (const wp of mission.waypoints) {
      const leg = this.world.route(from, wp)
      for (const name of leg) {
        if (names.length === 0 || names[names.length - 1] !== name) names.push(name)
      }
      from = wp
    }
    if (names.length === 0) names.push(this.location)
    this.path = buildPath(names, this.world)
    this.distance = this.distance < 0 ? this.distance : 0
    this.reachedIndex = 0
    this.mission = { id: mission.id, status: 'running' }
    this.routine = routineFor(mission.routine, mission.routineSteps)
    this.routineActive = false
    this.routineIndex = 0
    this.routineTimer = 0
    this.holdUntil = undefined
    this.state = 'moving'
    this.speedKmh = mission.speedKmh ?? this.speedKmh
  }

  hold(durationS?: number, simTime = 0): void {
    this.state = 'held'
    this.holdUntil = durationS === undefined ? undefined : simTime + durationS
    if (this.mission) this.mission.status = 'held'
  }

  resume(): void {
    this.holdUntil = undefined
    if (this.state === 'held') this.state = 'moving'
    if (this.mission) this.mission.status = 'running'
  }

  stop(reason: 'assist' | 'hold'): void {
    this.state = 'held'
    this.holdUntil = undefined
    if (this.mission) this.mission.status = 'held'
    void reason
  }

  setFault(step?: string): void {
    this.faultStep = step
  }

  isMoving(): boolean {
    return this.state === 'moving'
  }

  get missionId(): string | undefined {
    return this.mission?.id
  }

  position(): { x: number; y: number; yaw: number } {
    return positionAt(this.path, this.distance)
  }

  get progress(): number {
    if (this.path.total === 0) return 0
    return Math.min(1, Math.max(0, this.distance / this.path.total))
  }

  snapshot(): CarSnapshot {
    const p = this.position()
    return {
      vin: this.vin,
      x: p.x,
      y: p.y,
      yaw: p.yaw,
      speedKmh: this.state === 'moving' ? this.speedKmh : 0,
      state: this.state,
      colour: this.colour,
      lamps: {
        headlights:
          this.normalHeadlights && this.faultStep !== 'headlights' && this.faultStep !== 'lamps',
        brake_lights:
          this.normalBrake && this.faultStep !== 'brake_lights' && this.faultStep !== 'lamps',
      },
      fault: this.faultStep,
    }
  }

  update(dt: number, simTime: number): SimEvent[] {
    const events: SimEvent[] = []
    events.push(...this.updateRoutine(dt))

    if (this.state === 'held') {
      if (this.holdUntil !== undefined && simTime >= this.holdUntil) {
        this.holdUntil = undefined
        this.state = 'moving'
        if (this.mission) this.mission.status = 'running'
      } else {
        return events
      }
    }
    if (this.state !== 'moving') return events

    const mps = (this.speedKmh * 1000) / 3600
    let next = this.distance + mps * dt

    while (
      this.reachedIndex < this.path.points.length - 1 &&
      next >= (this.path.cumulative[this.reachedIndex + 1] as number)
    ) {
      this.reachedIndex++
      const loc = (this.path.points[this.reachedIndex] as Point).name
      this.location = loc
      events.push({ type: 'Arrived', vin: this.vin, location: loc })
    }

    if (next >= this.path.total) {
      next = this.path.total
      this.distance = next
      this.location = (this.path.points[this.path.points.length - 1] as Point).name
      this.state = 'stopped'
      if (this.mission) this.mission.status = 'done'
      events.push({ type: 'MissionDone', vin: this.vin, missionId: this.mission?.id ?? '' })
      events.push(...this.startRoutine())
    } else {
      this.distance = next
    }
    return events
  }

  private applyLamps(step: RoutineStep): void {
    switch (step.name) {
      case 'headlights_on':
        this.normalHeadlights = true
        this.normalBrake = false
        break
      case 'headlights_off':
        this.normalHeadlights = false
        this.normalBrake = false
        break
      case 'brake_lights':
        this.normalBrake = true
        break
      case 'reverse':
        this.normalBrake = false
        break
      case 'brake_test':
        this.normalBrake = true
        break
      default:
        break
    }
  }

  private startRoutine(): SimEvent[] {
    if (this.routine.length === 0) return []
    this.routineActive = true
    this.routineIndex = 0
    this.routineTimer = 0
    const step = this.routine[0] as RoutineStep
    this.applyLamps(step)
    return [
      { type: 'TrackStep', vin: this.vin, step: 0, name: step.name, telemetry: step.telemetry },
    ]
  }

  private updateRoutine(dt: number): SimEvent[] {
    if (!this.routineActive) return []
    const events: SimEvent[] = []
    this.routineTimer += dt
    let step = this.routine[this.routineIndex] as RoutineStep
    while (this.routineActive && this.routineTimer >= step.durationS) {
      this.routineTimer -= step.durationS
      this.routineIndex++
      if (this.routineIndex >= this.routine.length) {
        this.routineActive = false
        events.push({ type: 'RoutineDone', vin: this.vin })
        break
      }
      step = this.routine[this.routineIndex] as RoutineStep
      this.applyLamps(step)
      events.push({
        type: 'TrackStep',
        vin: this.vin,
        step: this.routineIndex,
        name: step.name,
        telemetry: step.telemetry,
      })
    }
    return events
  }
}
