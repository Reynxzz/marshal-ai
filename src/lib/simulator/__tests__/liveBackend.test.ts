// The live backend on the dashboard store: the default demo shift runs end to end on the
// ThreeJsEngine (animated parking, lane approach, live camera ports). jsdom has no WebGL, so the
// camera ports return no frames and the image checks answer offline; driving is the engine's.

import { describe, expect, it } from 'vitest'
import { POINTS } from '#/lib/marshal/points'
import { enterableSimSlot } from '#/lib/marshal/state'
import { createOfflineMarshalAi } from '#/lib/marshal/testing/offlineAi'
import type { AssistRequestEvent } from '#/lib/marshal/types'
import { useShiftStore } from '#/stores/shift'
import { ThreeJsEngine } from '../engine'
import { createLiveBackend, LIVE_ASSIST_FRAMES, type LiveBackend } from '../liveBackend'

async function flush(): Promise<void> {
  for (let i = 0; i < 50; i++) await Promise.resolve()
}

describe('live backend', () => {
  it('declares real frames and the yard-lane approach', () => {
    const backend = createLiveBackend()
    expect(backend.placeholderFrames).toBe(false)
    expect(backend.laneApproach).toBe(true)
    expect(backend).toBeInstanceOf(ThreeJsEngine)
  })

  it('returns empty frames without WebGL instead of throwing', async () => {
    const backend = createLiveBackend()
    backend.spawn('VIN-1', { at: 'inspection_gate', colour: [0, 0, 1] })
    const frames = await backend.capture('VIN-1', ['front', 'rear'])
    expect(frames).toEqual({ front: '', rear: '', left: '', right: '' })
    expect(backend.getEvidenceFrames('VIN-1', 'front')).toEqual([])
    backend.dispose()
  })

  it('sends the approach to the obstacle as the assist clip, not one still repeated', () => {
    // A fake camera: each frame names where the chase camera stood.
    const backend = createLiveBackend({
      shoot: (pose) => `${pose.x.toFixed(1)},${pose.y.toFixed(1)}`,
    })
    const requests: AssistRequestEvent[] = []
    backend.on('assist.request', (event) => requests.push(event))
    backend.spawn('VIN-1', { at: 'line_end', colour: [0, 0, 1] })
    backend.spawnProp('box-1', 'box', 30, POINTS.line_end.y, 0)
    backend.send({
      op: 'mission.go',
      mission_id: 'M1',
      vin: 'VIN-1',
      waypoints: ['inspection_gate'],
    })
    for (let t = 0; t < 120 && requests.length === 0; t++) backend.tick(0.5)

    expect(requests).toHaveLength(1)
    const clip = requests[0].clip_frames
    expect(clip.length).toBeGreaterThan(1)
    expect(clip.length).toBeLessThanOrEqual(LIVE_ASSIST_FRAMES)
    expect(new Set(clip).size).toBe(clip.length)
    // Oldest first, ending where the car stopped.
    const stop = backend.getPositionXY('VIN-1')
    const last = clip[clip.length - 1].split(',').map(Number)
    expect(Math.hypot(last[0] - (stop.x - 6), last[1] - stop.y)).toBeLessThan(0.2)
    const xs = clip.map((frame) => Number(frame.split(',')[0]))
    expect([...xs].sort((a, b) => a - b)).toEqual(xs)
    backend.dispose()
  })

  it('plays the default shift: every good car parks in its slot', async () => {
    let backend: LiveBackend | null = null
    const store = useShiftStore.getState()
    store.setPauseWhenNeeded(false)
    store.configure({
      ai: createOfflineMarshalAi(),
      backend: () => {
        backend = createLiveBackend()
        return backend
      },
    })
    for (let t = 0; t < 900 && useShiftStore.getState().shift.progress < 1; t++) {
      useShiftStore.getState().advance(1)
      await flush()
    }
    const { cars, layout } = useShiftStore.getState().shift
    // P003 failed the gate on colour: repainted, re-checked, then parked like the rest.
    expect(cars.P003.colour).toBe(cars.P003.expectedColour)
    for (const vin of ['P001', 'P002', 'P003', 'P004', 'P005']) {
      expect(cars[vin].stage).toBe('parked')
      const slot = cars[vin].slot ?? ''
      expect(layout[slot]).toBe(vin)
      // Parked where the slot really is (within the parking tolerance).
      const point = (backend as unknown as LiveBackend).getTransform(`yard.Y${slot.slice(1)}`)
      const pose = (backend as unknown as LiveBackend).getPositionXY(vin)
      expect(Math.hypot(pose.x - point.x, pose.y - point.y)).toBeLessThan(0.6)
    }
  }, 60_000)
})

describe('right of way', () => {
  it('finishes the shift when AI answers arrive late (no two cars waiting on each other)', async () => {
    // With answers 0.5 s late the car back from rework met the one leaving the report point where
    // those two straight legs cross; each had the other in front and both waited for ever.
    let now = 0
    const waiting: { at: number; resolve: () => void }[] = []
    const base = createOfflineMarshalAi()
    const ai = Object.fromEntries(
      Object.entries(base).map(([name, call]) => [
        name,
        async (input: unknown) => {
          await new Promise<void>((resolve) => waiting.push({ at: now + 0.5, resolve }))
          return (call as (value: unknown) => unknown)(input)
        },
      ]),
    ) as unknown as ReturnType<typeof createOfflineMarshalAi>
    const store = useShiftStore.getState()
    store.setPauseWhenNeeded(false)
    store.configure({ ai, backend: () => createLiveBackend() })
    for (let t = 0; t < 2400 && useShiftStore.getState().shift.progress < 1; t++) {
      useShiftStore.getState().advance(0.25)
      now += 0.25
      for (const item of waiting.filter((entry) => entry.at <= now)) {
        item.resolve()
        waiting.splice(waiting.indexOf(item), 1)
      }
      await flush()
    }
    expect(useShiftStore.getState().shift.progress).toBe(1)
  }, 60_000)
})

describe('engine additions for the live backend', () => {
  it('starts a staged car from the line end, not from a closer yard slot', () => {
    const engine = new ThreeJsEngine()
    // 36 m behind line_end: closer to yard.Y8 (34.9 m) than to line_end (36 m).
    engine.spawn('VIN-5', { at: 'line_end', colour: [0, 0, 0], behind_m: 36 })
    engine.send({
      op: 'mission.go',
      mission_id: 'M-1',
      vin: 'VIN-5',
      waypoints: ['buffer_zone_b'],
      max_speed_kmh: 10,
    })
    for (let i = 0; i < 40; i++) engine.tick(0.5)
    const pose = engine.getPositionXY('VIN-5')
    // Drove up the staging lane towards line_end (y stays on the line) instead of the yard.
    expect(pose.y).toBeCloseTo(5.25, 0)
    expect(pose.x).toBeGreaterThan(-28)
  })

  it('animates parking steps over sim time when asked to', async () => {
    const engine = new ThreeJsEngine(undefined, { animateParking: true })
    engine.spawn('VIN-P', { at: 'yard.Y1', colour: [0, 0, 1] })
    const before = engine.getPositionXY('VIN-P')
    let done = false
    const result = engine
      .execute('VIN-P', [{ speed_mps: 1, wheel: 0, duration_s: 2, gear: 'R' }])
      .then((value) => {
        done = true
        return value
      })
    await flush()
    expect(done).toBe(false)
    engine.tick(1)
    expect(engine.getPositionXY('VIN-P').y).toBeCloseTo(before.y - 1, 1)
    expect(engine.lampsOf('VIN-P').reverse).toBe(true)
    engine.tick(1.2)
    const end = await result
    expect(end.stopped).toBe(false)
    expect(end.pose.y).toBeCloseTo(before.y - 2, 5)
    expect(engine.lampsOf('VIN-P').reverse).toBe(false)
  })

  it('lists cars and props for the camera scene', () => {
    const engine = new ThreeJsEngine()
    engine.spawn('VIN-1', { at: 'yard.Y3', colour: [1, 0, 0] })
    engine.spawnProp('box-1', 'box', 1, 2, 30)
    expect(engine.listCars()).toEqual([
      { vin: 'VIN-1', x: -30.75, y: -57, yaw: 90, rgb: [1, 0, 0] },
    ])
    expect(engine.listProps()).toEqual([{ id: 'box-1', kind: 'box', x: 1, y: 2, yaw: 30 }])
  })
})

describe('enterableSimSlot', () => {
  const stateWith = (layout: Record<string, string | null>) => {
    const state = useShiftStore.getState().shift
    const empty = Object.fromEntries(Object.keys(state.layout).map((slot) => [slot, null]))
    return { ...state, cars: {}, reserved: [], layout: { ...empty, ...layout } }
  }

  it('fills the row from the far end', () => {
    expect(enterableSimSlot(stateWith({}))).toBe('A8')
    expect(enterableSimSlot(stateWith({ A8: 'X' }))).toBe('A7')
  })

  it('never picks a slot with a car right behind it while another is enterable', () => {
    // A5 and A8 taken: A6 has a car behind it; A7 (rear A6 free) is the deepest enterable slot.
    expect(enterableSimSlot(stateWith({ A5: 'X', A8: 'Y' }))).toBe('A7')
    // Every other slot taken: only A1 (nothing behind it) can still be driven into.
    expect(enterableSimSlot(stateWith({ A2: 'X', A4: 'Y', A6: 'Z', A8: 'W' }))).toBe('A1')
    // Nothing enterable left: a walled-in slot is the last resort.
    expect(enterableSimSlot(stateWith({ A1: 'V', A2: 'X', A4: 'Y', A6: 'Z', A8: 'W' }))).toBe('A7')
  })

  it('keeps a requested slot when it is free', () => {
    expect(enterableSimSlot(stateWith({}), 'A3')).toBe('A3')
  })
})
