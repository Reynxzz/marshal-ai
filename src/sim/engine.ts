// Deterministic simulation engine: fixed-step tick loop over vehicles moving on the world map.
// Pure TypeScript. No three.js, no React, no I/O, no wall-clock.

import type { SimEvent } from './events'
import { makeRng, type Rng } from './rng'
import { type CarSnapshot, type MissionSpec, Vehicle } from './vehicle'
import type { World } from './world'

export type MissionGo = { type: 'go'; vin: string; mission: MissionSpec }
export type MissionHold = { type: 'hold'; vin: string; durationS?: number }
export type MissionResume = { type: 'resume'; vin: string }
export type MissionCmd = MissionGo | MissionHold | MissionResume

export const FIXED_DT = 0.1
const MIN_GAP_M = 5
const QUEUE_SPACING_M = 9
const STATUS_INTERVAL_S = 1

export class SimEngine {
  readonly world: World
  readonly rng: Rng
  timeScale: number
  simTime = 0

  private readonly vehicles = new Map<string, Vehicle>()
  private readonly listeners = new Set<(ev: SimEvent) => void>()
  private readonly spawnCounts = new Map<string, number>()
  private lastStatusAt = Number.NEGATIVE_INFINITY

  constructor(world: World, opts: { seed: number; timeScale?: number }) {
    this.world = world
    this.rng = makeRng(opts.seed)
    this.timeScale = opts.timeScale ?? 1
  }

  on(cb: (ev: SimEvent) => void): () => void {
    this.listeners.add(cb)
    return () => {
      this.listeners.delete(cb)
    }
  }

  spawn(vin: string, at = 'line_end', opts: { colour?: string; speedKmh?: number } = {}): void {
    const count = this.spawnCounts.get(at) ?? 0
    this.spawnCounts.set(at, count + 1)
    this.vehicles.set(
      vin,
      new Vehicle(vin, this.world, at, { ...opts, queueOffsetM: count * QUEUE_SPACING_M }),
    )
  }

  despawn(vin: string): void {
    this.vehicles.delete(vin)
  }

  get(vin: string): Vehicle | undefined {
    return this.vehicles.get(vin)
  }

  send(cmd: MissionCmd): void {
    const v = this.vehicles.get(cmd.vin)
    if (!v) return
    if (cmd.type === 'go') v.dispatch(cmd.mission)
    else if (cmd.type === 'hold') v.hold(cmd.durationS, this.simTime)
    else v.resume()
  }

  stop(vin: string, reason: 'assist' | 'hold'): void {
    this.vehicles.get(vin)?.stop(reason)
  }

  resume(vin: string): void {
    this.vehicles.get(vin)?.resume()
  }

  setFault(vin: string, fault: { step: string }): void {
    this.vehicles.get(vin)?.setFault(fault.step)
  }

  clearFault(vin: string): void {
    this.vehicles.get(vin)?.setFault(undefined)
  }

  snapshot(): CarSnapshot[] {
    return [...this.vehicles.values()]
      .sort((a, b) => (a.vin < b.vin ? -1 : a.vin > b.vin ? 1 : 0))
      .map((v) => v.snapshot())
  }

  step(dt: number): void {
    this.simTime += dt
    const order = [...this.vehicles.values()].sort((a, b) =>
      a.vin < b.vin ? -1 : a.vin > b.vin ? 1 : 0,
    )
    const positions = new Map(order.map((v) => [v.vin, v.position()] as const))

    for (const v of order) {
      if (v.isMoving() && this.forwardBlocked(v, positions)) continue
      this.emitAll(v.update(dt, this.simTime))
    }

    if (this.simTime - this.lastStatusAt >= STATUS_INTERVAL_S) {
      this.lastStatusAt = this.simTime
      for (const v of order) {
        const p = v.position()
        this.emitAll([
          {
            type: 'Status',
            vin: v.vin,
            location: v.location,
            x: p.x,
            y: p.y,
            speedKmh: v.isMoving() ? v.speedKmh : 0,
            missionId: v.missionId,
            progress: v.progress,
          },
        ])
      }
    }
  }

  private forwardBlocked(
    v: Vehicle,
    positions: Map<string, { x: number; y: number; yaw: number }>,
  ): boolean {
    const me = positions.get(v.vin)
    if (!me) return false
    const hx = Math.cos(me.yaw)
    const hy = Math.sin(me.yaw)
    for (const [vin, op] of positions) {
      if (vin === v.vin) continue
      const dx = op.x - me.x
      const dy = op.y - me.y
      const dist = Math.hypot(dx, dy)
      if (dist < MIN_GAP_M && dx * hx + dy * hy > 0) return true
    }
    return false
  }

  private emitAll(events: SimEvent[]): void {
    for (const ev of events) {
      for (const cb of this.listeners) cb(ev)
    }
  }
}
