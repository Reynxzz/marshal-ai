// Test harness: orchestrator + ScriptedDrivingSystem + offline MarshalAi on a private bus.

import type { MarshalAi } from '../../serverFns/client'
import { createEventBus, type MarshalEventMap, type MarshalNote } from '../eventBus'
import { createOrchestrator } from '../orchestrator'
import type { MarshalState } from '../state'
import { createOfflineMarshalAi } from '../testing/offlineAi'
import { carsOverlap, tooCloseInLane } from '../testing/overlap'
import { ScriptedDrivingSystem } from '../testing/scriptedSystem'
import type { MissionGoCmd, SimCommand } from '../types'

/**
 * Builds a fully offline orchestrator.
 * @param opts - Pause toggle, scripted AI overrides, whether the engine's truth hints are
 * wired (default true; false mimics a real engine with no hints) and an optional engine to use.
 * @returns Engine, orchestrator and captured bus traffic.
 */
export function setup(
  opts: {
    pauseWhenNeeded?: boolean
    ai?: Partial<MarshalAi>
    hints?: boolean
    ds?: ScriptedDrivingSystem
  } = {},
) {
  const ds = opts.ds ?? new ScriptedDrivingSystem()
  const bus = createEventBus<MarshalEventMap>()
  const notes: MarshalNote[] = []
  const commands: SimCommand[] = []
  bus.on('ui:note', (note) => notes.push(note))
  bus.on('marshal:command', (cmd) => commands.push(cmd))
  const orch = createOrchestrator({
    ds,
    gate: ds,
    evidence: ds,
    parking: ds,
    hints: opts.hints === false ? undefined : ds,
    ai: createOfflineMarshalAi(opts.ai),
    bus,
    clock: () => ds.time,
    pauseWhenNeeded: opts.pauseWhenNeeded ?? true,
  })
  const missionsOf = (vin: string) =>
    commands.filter((cmd): cmd is MissionGoCmd => cmd.op === 'mission.go' && cmd.vin === vin)
  const feedText = (vin?: string) => {
    const feed = orch.getState().feed.filter((entry) => vin === undefined || entry.vin === vin)
    return feed.map((entry) => `${entry.tag} ${entry.text}`)
  }
  return { ds, bus, orch, notes, commands, missionsOf, feedText }
}

/**
 * Simulated AI latency in sim seconds: wrapped calls resolve once `pump()` sees the clock pass
 * their due time (no timers, so tests stay deterministic).
 * @param clock - Sim clock in seconds.
 * @returns `wrap(ai, seconds)` and `pump()` (call after every engine tick).
 */
export function simLatency(clock: () => number) {
  const pending: { due: number; resolve: () => void }[] = []
  const wait = (seconds: number) =>
    new Promise<void>((resolve) => pending.push({ due: clock() + seconds, resolve }))
  const wrap = (ai: MarshalAi, seconds: number): MarshalAi => {
    const out: Record<string, unknown> = {}
    for (const [name, fn] of Object.entries(ai) as [
      string,
      (input: unknown) => Promise<unknown>,
    ][]) {
      out[name] = async (input: unknown) => {
        await wait(seconds)
        return fn(input)
      }
    }
    return out as MarshalAi
  }
  const pump = () => {
    const now = clock()
    for (let i = pending.length - 1; i >= 0; i--) {
      if (pending[i].due > now + 1e-9) continue
      pending[i].resolve()
      pending.splice(i, 1)
    }
  }
  return { wrap, pump }
}

/** One overlap (or too-close) episode between two live cars. */
export type OverlapEpisode = {
  vins: [string, string]
  kind: 'overlap' | 'close'
  from: number
  to: number
  /** Car locations / stages / missions when the episode started. */
  where: string
}

/** Pose source and state used by {@link OverlapMonitor}. */
type Watched = {
  getPositionXY: ScriptedDrivingSystem['getPositionXY']
  missionOf: ScriptedDrivingSystem['missionOf']
}

/**
 * Records every pair of live cars (not shipped, not despawned) whose footprints overlap or that
 * are under 5 m apart in the same lane.
 */
export class OverlapMonitor {
  readonly episodes: OverlapEpisode[] = []
  private readonly open = new Map<string, OverlapEpisode>()

  /**
   * @param tolerance_m - Footprints are shrunk by this much before the overlap test.
   */
  constructor(private readonly tolerance_m = 0) {}

  /**
   * Samples the current poses.
   * @param ds - Engine.
   * @param state - Orchestrator snapshot.
   * @param time - Sim time of the sample.
   */
  sample(ds: Watched, state: MarshalState, time: number): void {
    const live = state.order.filter((vin) => {
      const pose = ds.getPositionXY(vin)
      const gone = pose.x === 0 && pose.y === 0 && pose.yaw === 0
      return state.cars[vin] && state.cars[vin].stage !== 'shipped' && !gone
    })
    const seen = new Set<string>()
    for (let i = 0; i < live.length; i++) {
      for (let j = i + 1; j < live.length; j++) {
        const a = ds.getPositionXY(live[i])
        const b = ds.getPositionXY(live[j])
        const overlap = carsOverlap(a, b, this.tolerance_m)
        if (!overlap && !tooCloseInLane(a, b)) continue
        const key = `${live[i]}|${live[j]}`
        seen.add(key)
        const current = this.open.get(key)
        if (current) {
          current.to = time
          if (overlap) current.kind = 'overlap'
          continue
        }
        const describe = (vin: string, pose: { x: number; y: number; yaw: number }) => {
          const car = state.cars[vin]
          const mission = ds.missionOf(vin)?.waypoints.join('>') ?? '-'
          const at = `(${pose.x.toFixed(1)}, ${pose.y.toFixed(1)}, ${pose.yaw.toFixed(0)}°)`
          return `${vin} ${car.stage}@${car.location} mission ${mission} ${at}`
        }
        const episode: OverlapEpisode = {
          vins: [live[i], live[j]],
          kind: overlap ? 'overlap' : 'close',
          from: time,
          to: time,
          where: `${describe(live[i], a)} | ${describe(live[j], b)}`,
        }
        this.open.set(key, episode)
        this.episodes.push(episode)
      }
    }
    for (const key of [...this.open.keys()]) if (!seen.has(key)) this.open.delete(key)
  }

  /** @returns Episodes where the footprints actually intersect. */
  overlaps(): OverlapEpisode[] {
    return this.episodes.filter((episode) => episode.kind === 'overlap')
  }

  /** @returns One line per episode. */
  report(): string[] {
    return this.episodes.map(
      (e) => `${e.kind} ${e.from.toFixed(2)}-${e.to.toFixed(2)}s ${e.vins.join('+')}: ${e.where}`,
    )
  }
}

/**
 * Drains pending promise callbacks (all ports and the offline AI resolve immediately).
 */
export async function flush(): Promise<void> {
  for (let i = 0; i < 50; i++) await Promise.resolve()
}

/**
 * Ticks the engine until `done` holds.
 * @param ds - Scripted engine.
 * @param getState - State getter.
 * @param done - Stop condition.
 * @param maxSeconds - Sim time budget.
 * @returns Whether the condition was met.
 */
export async function runUntil(
  ds: ScriptedDrivingSystem,
  getState: () => MarshalState,
  done: (state: MarshalState) => boolean,
  maxSeconds = 600,
): Promise<boolean> {
  const dt = 0.5
  for (let t = 0; t < maxSeconds; t += dt) {
    await flush()
    if (done(getState())) return true
    ds.tick(dt)
  }
  await flush()
  return done(getState())
}
