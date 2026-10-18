// M10 acceptance: the five demo scenarios run end-to-end on the real ThreeJsEngine with the
// Marshal orchestrator and the offline AI. Gate/evidence ports come from the scripted engine
// (its camera frames are placeholders); driving, parking and truth hints are the engine's.

import { describe, expect, it } from 'vitest'
import { stageHazard } from '#/lib/marshal/hazards'
import { createOrchestrator, type Orchestrator } from '#/lib/marshal/orchestrator'
import type { MarshalState } from '#/lib/marshal/state'
import { createOfflineMarshalAi } from '#/lib/marshal/testing/offlineAi'
import { ScriptedDrivingSystem } from '#/lib/marshal/testing/scriptedSystem'
import { ThreeJsEngine } from '../engine'

async function flush(): Promise<void> {
  for (let i = 0; i < 50; i++) await Promise.resolve()
}

function scenario() {
  const engine = new ThreeJsEngine()
  const ports = new ScriptedDrivingSystem()
  const orch: Orchestrator = createOrchestrator({
    ds: engine,
    gate: ports,
    evidence: ports,
    parking: engine,
    hints: engine,
    ai: createOfflineMarshalAi(),
    clock: () => engine.time,
    pauseWhenNeeded: false,
  })
  return { engine, orch }
}

async function runUntil(
  orch: Orchestrator,
  engine: ThreeJsEngine,
  done: (state: MarshalState) => boolean,
  maxSeconds = 1800,
): Promise<boolean> {
  for (let elapsed = 0; elapsed < maxSeconds; elapsed += 0.5) {
    await flush()
    if (done(orch.getState())) return true
    engine.tick(0.5)
  }
  await flush()
  return done(orch.getState())
}

describe('demo scenarios on the engine (M10)', () => {
  it('Scene A: hazard reroutes via Lane C and the cars park / go to rework (M10.1)', async () => {
    const { engine, orch } = scenario()
    const hazard = stageHazard(engine, 'finishing_corner', 'forklift_unloading')
    orch.setHazard(hazard.description)
    orch.start([
      { vin: 'VIN-B1', colour: 'blue' },
      { vin: 'VIN-R1', colour: 'red', expectedColour: 'blue' },
    ])

    const done = await runUntil(
      orch,
      engine,
      (state) => state.cars['VIN-B1'].stage === 'parked' && state.cars['VIN-R1'].stage === 'rework',
    )
    expect(done).toBe(true)
    const state = orch.getState()
    expect(state.cars['VIN-B1'].slot).toBeDefined()
    expect(state.cars['VIN-R1'].reworkReason).toBe('gate')
    const texts = state.feed.map((entry) => entry.text)
    expect(texts.some((text) => text.startsWith('Route via lane_c'))).toBe(true)
    expect(texts.some((text) => text.includes('colour_mismatch'))).toBe(true)
  })

  it('Scene B: dead headlight fails R-12, is repaired and re-checked (M10.2)', async () => {
    const { engine, orch } = scenario()
    orch.start([{ vin: 'VIN-DARK', colour: 'blue' }])
    engine.setFault('VIN-DARK', new Set(['headlights']))

    const parked = await runUntil(
      orch,
      engine,
      (state) => state.cars['VIN-DARK'].stage === 'parked',
    )
    expect(parked).toBe(true)
    const texts = orch.getState().feed.map((entry) => entry.text)
    expect(texts.some((text) => /step 1 headlights: .* -> FAIL/.test(text))).toBe(true)
    expect(texts.some((text) => text.startsWith('Rework repaired headlights'))).toBe(true)
    expect(texts.some((text) => /step 1 headlights: .* -> PASS/.test(text))).toBe(true)
    expect(engine.faultsOf('VIN-DARK')).toEqual([])
  })

  it('Scene C: an obstacle raises an assist that is applied and the car resumes (M10.3)', async () => {
    const { engine, orch } = scenario()
    orch.start([{ vin: 'VIN-STUCK', colour: 'blue' }])
    engine.spawnProp('box-1', 'box', 20, 5.25, 0)

    const opened = await runUntil(
      orch,
      engine,
      (state) => state.cases['AC-VIN-STUCK-1'] !== undefined,
    )
    expect(opened).toBe(true)
    expect(orch.getState().cases['AC-VIN-STUCK-1']).toMatchObject({
      vin: 'VIN-STUCK',
      status: 'decided',
      chosen_by: 'gemini',
    })
    const parked = await runUntil(
      orch,
      engine,
      (state) => state.cars['VIN-STUCK'].stage === 'parked',
    )
    expect(parked).toBe(true)
    expect(engine.assistOutcomes['AC-VIN-STUCK-1']).toMatchObject({ option: 'pass_left_3kmh' })
  })

  it('Scene D: a late truck re-plans the yard and the car moves slot to slot (M10.4)', async () => {
    const { engine, orch } = scenario()
    orch.start([{ vin: 'P090', colour: 'green', parkedAt: 'A5' }], {
      trucks: [{ id: 'T-1', state: 'loading', slots: ['A5'] }],
    })
    const outcome = await orch.applyYardReplan(
      { priorityVins: ['P090'], reason: 'Late truck T-1: P090 moves up' },
      { truck: { id: 'T-1', state: 'late' } },
    )
    expect(outcome.ordered.length).toBeGreaterThan(0)
    expect(orch.getState().trucks[0].state).toBe('late')

    const moved = await runUntil(
      orch,
      engine,
      (state) => state.cars['P090'].stage === 'parked' && state.layout.A5 !== 'P090',
    )
    expect(moved).toBe(true)
    expect(orch.getState().layout[orch.getState().cars['P090'].slot ?? '']).toBe('P090')
  })

  it('Scene E: parks in A7 behind the car parked in A8 (M10.5)', async () => {
    const { engine, orch } = scenario()
    orch.start([
      { vin: 'VIN-Y8', colour: 'white', parkedAt: 'A8' },
      { vin: 'VIN-E', colour: 'blue', targetSlot: 'A7' },
    ])

    const parked = await runUntil(orch, engine, (state) => state.cars['VIN-E'].stage === 'parked')
    expect(parked).toBe(true)
    expect(orch.getState().layout).toMatchObject({ A7: 'VIN-E', A8: 'VIN-Y8' })
    const texts = orch.getState().feed.map((entry) => entry.text)
    expect(texts.some((text) => /^VIN-E parking Y7: path [ABC]/.test(text))).toBe(true)
  })

  it('stores artifacts with the memory fallback (M10.7)', async () => {
    const engine = new ThreeJsEngine()
    await expect(
      engine.storeArtifact('marshal://evidence/VIN-1.jpg', new Blob(['frame'])),
    ).resolves.toBeUndefined()
  })
})
