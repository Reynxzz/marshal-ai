import { describe, expect, it, vi } from 'vitest'
import { offlineAssist, offlineGate, offlineLamp } from '../../serverFns/logic'
import { fitFrames, fitGateFrames } from '../frames'
import {
  type AssistInput,
  type GateInput,
  type LampInput,
  MAX_FRAME_CHARS,
  MAX_FRAMES,
} from '../schemas'
import { ScriptedDrivingSystem } from '../testing/scriptedSystem'
import type { AssistRequestEvent, SimEvent } from '../types'
import { flush, runUntil, setup } from './harness'

const huge = 'A'.repeat(MAX_FRAME_CHARS + 1)
const seq = (n: number) => Array.from({ length: n }, (_, i) => `f${i}`)

describe('fitFrames', () => {
  it('keeps frames within the limits unchanged', () => {
    expect(fitFrames(seq(3))).toEqual({ frames: seq(3), oversized: 0, overflow: 0 })
  })

  it('keeps the most recent MAX_FRAMES frames', () => {
    const fitted = fitFrames(seq(12))
    expect(fitted.frames).toEqual(seq(12).slice(12 - MAX_FRAMES))
    expect(fitted).toMatchObject({ oversized: 0, overflow: 12 - MAX_FRAMES })
  })

  it('drops oversized frames before keeping the most recent ones', () => {
    const frames = [...seq(10), huge]
    const fitted = fitFrames(frames)
    expect(fitted.frames).toEqual(seq(10).slice(10 - MAX_FRAMES))
    expect(fitted).toMatchObject({ oversized: 1, overflow: 10 - MAX_FRAMES })
    expect(fitFrames([huge, huge])).toEqual({ frames: [], oversized: 2, overflow: 0 })
  })

  it('drops oversized gate views', () => {
    const fitted = fitGateFrames({ front: huge, rear: 'r', left: 'l', right: huge })
    expect(fitted).toEqual({ frames: { rear: 'r', left: 'l' }, dropped: ['front', 'right'] })
  })
})

describe('flows trim frames before calling the server functions', () => {
  it('gate: sends only the views that fit', async () => {
    const ds = new ScriptedDrivingSystem()
    ds.capture = async () => ({ front: huge, rear: 'r', left: 'l', right: 'x' })
    const evaluateGatePhotos = vi.fn(async (input: GateInput) => offlineGate(input))
    const { orch, feedText } = setup({ ds, ai: { evaluateGatePhotos } })
    orch.start([{ vin: 'VIN-G', colour: 'blue' }])
    await runUntil(ds, orch.getState, (s) => s.cars['VIN-G'].stage === 'test_track')
    expect(evaluateGatePhotos).toHaveBeenCalledTimes(1)
    expect(evaluateGatePhotos.mock.calls[0][0].frames).toEqual({ rear: 'r', left: 'l', right: 'x' })
    expect(feedText('VIN-G')).toContain('PLANT Gate view(s) too large to send, dropped: front')
  })

  it('gate: answers offline without a call when no view fits', async () => {
    const ds = new ScriptedDrivingSystem()
    ds.capture = async () => ({ front: huge, rear: huge, left: huge, right: huge })
    const evaluateGatePhotos = vi.fn(async (input: GateInput) => offlineGate(input))
    const { orch, feedText } = setup({ ds, ai: { evaluateGatePhotos } })
    orch.start([{ vin: 'VIN-G', colour: 'blue' }])
    const moved = await runUntil(ds, orch.getState, (s) => s.cars['VIN-G'].stage === 'test_track')
    expect(moved).toBe(true)
    expect(evaluateGatePhotos).not.toHaveBeenCalled()
    const pass = orch.getState().feed.find((entry) => entry.text.startsWith('Gate PASS'))
    expect(pass?.degraded).toBe(true)
    expect(feedText('VIN-G').some((line) => line.includes('dropped: front, rear'))).toBe(true)
  })

  it('lamp: sends the most recent frames that fit', async () => {
    const ds = new ScriptedDrivingSystem()
    ds.getEvidenceFrames = () => [...seq(11), huge]
    const evaluateLampEvidence = vi.fn(async (input: LampInput) => offlineLamp(input))
    const { orch, feedText } = setup({ ds, ai: { evaluateLampEvidence } })
    orch.start([{ vin: 'VIN-L', colour: 'blue' }])
    await runUntil(ds, orch.getState, () => evaluateLampEvidence.mock.calls.length > 0)
    expect(evaluateLampEvidence.mock.calls[0][0].frames).toEqual(seq(11).slice(11 - MAX_FRAMES))
    const dropped = 'PLANT R-12 step 1: dropped 1 evidence frame(s) too large to send'
    expect(feedText('VIN-L')).toContain(dropped)
  })

  it('lamp: answers offline without a call when every frame is too large', async () => {
    const ds = new ScriptedDrivingSystem()
    ds.getEvidenceFrames = () => [huge]
    const evaluateLampEvidence = vi.fn(async (input: LampInput) => offlineLamp(input))
    const { orch } = setup({ ds, ai: { evaluateLampEvidence } })
    orch.start([{ vin: 'VIN-L', colour: 'blue' }])
    const parked = await runUntil(ds, orch.getState, (s) => s.cars['VIN-L'].stage === 'parked')
    expect(parked).toBe(true)
    expect(evaluateLampEvidence).not.toHaveBeenCalled()
  })

  function captureAssist(ds: ScriptedDrivingSystem): () => (event: AssistRequestEvent) => void {
    type Listener = (event: AssistRequestEvent) => void
    let listener: Listener | undefined
    const on = ds.on.bind(ds) as unknown as (event: SimEvent['event'], cb: Listener) => void
    const spy = (event: SimEvent['event'], cb: Listener) => {
      if (event === 'assist.request') listener = cb
      on(event, cb)
    }
    ds.on = spy as unknown as typeof ds.on
    return () => {
      if (!listener) throw new Error('no assist listener')
      return listener
    }
  }

  const request = (clip_frames: string[]): AssistRequestEvent => ({
    event: 'assist.request',
    case_id: 'AC-VIN-A-1',
    vin: 'VIN-A',
    location: 'buffer_zone_b',
    reason: 'Box in the lane',
    options: ['wait_for_removal', 'pass_left_3kmh'],
    clip_frames,
  })

  it('assist: sends the most recent clip frames that fit', async () => {
    const ds = new ScriptedDrivingSystem()
    const listener = captureAssist(ds)
    const analyzeAssistClip = vi.fn(async (input: AssistInput) => offlineAssist(input))
    const { orch, feedText } = setup({ ds, ai: { analyzeAssistClip } })
    orch.start([{ vin: 'VIN-A', colour: 'blue' }])
    listener()(request([huge, ...seq(10)]))
    await flush()
    expect(analyzeAssistClip.mock.calls[0][0].frames).toEqual(seq(10).slice(10 - MAX_FRAMES))
    expect(feedText('VIN-A')).toContain(
      'PLANT AC-VIN-A-1: dropped 1 clip frame(s) too large to send',
    )
    // The case keeps the whole clip for the dashboard.
    expect(orch.getState().cases['AC-VIN-A-1'].frames).toHaveLength(11)
  })

  it('assist: answers offline without a call when every clip frame is too large', async () => {
    const ds = new ScriptedDrivingSystem()
    const listener = captureAssist(ds)
    const analyzeAssistClip = vi.fn(async (input: AssistInput) => offlineAssist(input))
    const { orch } = setup({ ds, ai: { analyzeAssistClip } })
    orch.start([{ vin: 'VIN-A', colour: 'blue' }])
    listener()(request([huge, huge]))
    await flush()
    expect(analyzeAssistClip).not.toHaveBeenCalled()
    const analysis = orch.getState().cases['AC-VIN-A-1'].analysis
    expect(analysis).toMatchObject({ recommendation: 'pass_left_3kmh', degraded: true })
  })
})
