import { offlineAi } from '#/ai/offline'
import type { AiTaskFn } from '#/ai/tasks'
import { truthCapture } from '#/ai/truth'
import { SimEngine } from '../engine'
import type { FlowHost } from '../flows/host'
import { makeRng } from '../rng'
import type { CarSnapshot } from '../vehicle'
import { world } from '../world'

export function makeHost(overrides: Partial<FlowHost> = {}): FlowHost {
  return {
    engine: new SimEngine(world, { seed: 1 }),
    world,
    ai: offlineAi,
    capture: truthCapture,
    rng: makeRng(1),
    batchId: 'BAT-T',
    emit: () => {},
    ...overrides,
  }
}

export function snap(
  vin: string,
  colour: string,
  lamps: { headlights: boolean; brake_lights: boolean } = { headlights: true, brake_lights: false },
): CarSnapshot {
  return { vin, x: 0, y: 0, yaw: 0, speedKmh: 0, state: 'stopped', colour, lamps }
}

export const missingHandlerAi: AiTaskFn = async () => {
  throw new Error('no handler')
}

/** Build an AiTaskFn from a simple (task, payload) implementation (tests only). */
export function stubAi(
  fn: (task: string, payload: Record<string, any>) => Promise<unknown>,
): AiTaskFn {
  return (async (task: string, payload: Record<string, any>) =>
    fn(task, payload)) as unknown as AiTaskFn
}
