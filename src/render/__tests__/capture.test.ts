import { describe, expect, it } from 'vitest'
import { GATE_VIEWS } from '#/ai/truth'
import type { CarSnapshot } from '#/sim/vehicle'
import { world } from '#/sim/world'
import { createCapture } from '../capture'

const car: CarSnapshot = {
  vin: 'VIN-TEST-001',
  x: 65,
  y: 40,
  yaw: 0,
  speedKmh: 0,
  state: 'stopped',
  colour: '#2f6fed',
  lamps: { headlights: true, brake_lights: false },
}

describe('capture', () => {
  it('returns four gate views with truth in truth mode', async () => {
    const capture = createCapture(world, { mode: 'truth' })
    const frames = await capture.gate(car)

    expect(frames).toHaveLength(4)
    expect(frames.map((frame) => frame.view)).toEqual([...GATE_VIEWS])
    for (const frame of frames) {
      expect(frame.truth).toContain('colour=blue')
      expect(frame.truth).toContain('headlights=on')
      expect(frame.dataUrl).toBe('')
    }
  })

  it('returns one track frame', async () => {
    const capture = createCapture(world, { mode: 'truth' })
    const frame = await capture.track(car, 'headlights_on')

    expect(frame.view).toBe('track-headlights_on')
    expect(frame.truth).toContain('headlights=on')
    expect(frame.truth).toContain('brake_lights=off')
  })

  it('falls back to truth without throwing when WebGL is unavailable', async () => {
    const capture = createCapture(world)
    const frames = await capture.gate(car)

    expect(frames).toHaveLength(4)
    expect(frames[0].truth).toContain('colour=blue')
  })
})
