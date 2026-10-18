import { describe, expect, it, vi } from 'vitest'
import { POINTS } from '#/lib/marshal/points'
import { type GateInput, gateInputSchema, MAX_FRAMES, type RouteInput } from '#/lib/marshal/schemas'
import type { RouteChoice } from '#/lib/marshal/types'
import { createMarshalAi } from '../client'
import {
  offlineAssist,
  offlineGate,
  offlineLamp,
  offlineParking,
  offlineRoute,
  offlineYard,
} from '../logic'

const gateInput: GateInput = {
  vin: 'V',
  expectedColour: 'blue',
  frames: { front: 'A', rear: 'A', left: 'A', right: 'A' },
  hints: { front: 'colour=red' },
}
const routeInput: RouteInput = {
  hazardDescription: 'spill',
  options: ['finishing_corner', 'lane_c'],
}

const fail = async (): Promise<never> => {
  throw new Error('network down')
}

describe('createMarshalAi', () => {
  it('returns the injected answer when send succeeds', async () => {
    const answer: RouteChoice = {
      route: 'finishing_corner',
      confidence: 0.9,
      reason: 'ok',
      degraded: false,
    }
    const chooseRouteHazard = vi.fn(async (_input: RouteInput) => answer)
    const ai = createMarshalAi({ chooseRouteHazard })
    await expect(ai.chooseRouteHazard(routeInput)).resolves.toEqual(answer)
    expect(chooseRouteHazard).toHaveBeenCalledWith(routeInput)
  })

  it('falls back offline (degraded) for every method when send throws', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const ai = createMarshalAi({
      evaluateGatePhotos: fail,
      evaluateLampEvidence: fail,
      analyzeAssistClip: fail,
      planParkingStep: fail,
      chooseRouteHazard: fail,
      replanYardSlots: fail,
    })
    const lampInput = {
      vin: 'V',
      step: 'headlights',
      telemetry: { headlights: 'ON' },
      frames: [],
      hint: 'headlights=off',
    }
    const assistInput = {
      case_id: 'c',
      reason: 'worker in lane',
      options: ['wait_for_removal', 'pass_left_3kmh'] as const,
      frames: [],
    }
    const slot = { id: 'yard.Y1', ...POINTS['yard.Y1'] }
    const parkingInput = { vin: 'V', pose: { x: slot.x, y: slot.y, yaw: 90 }, obstacles: [], slot }
    const yardInput = { layout: { 'yard.Y1': null, 'yard.Y2': 'V' }, constraints: {} }

    await expect(ai.evaluateGatePhotos(gateInput)).resolves.toEqual(offlineGate(gateInput))
    await expect(ai.evaluateLampEvidence(lampInput)).resolves.toEqual(offlineLamp(lampInput))
    const assistArgs = { ...assistInput, options: [...assistInput.options] }
    await expect(ai.analyzeAssistClip(assistArgs)).resolves.toEqual(offlineAssist(assistArgs))
    await expect(ai.planParkingStep(parkingInput)).resolves.toEqual(offlineParking(parkingInput))
    await expect(ai.chooseRouteHazard(routeInput)).resolves.toEqual(offlineRoute(routeInput))
    await expect(ai.replanYardSlots(yardInput)).resolves.toEqual(offlineYard(yardInput))

    const gate = await ai.evaluateGatePhotos(gateInput)
    expect(gate).toMatchObject({ pass: false, degraded: true })
    expect(gate.invalidInput).toBeUndefined()
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('answers offline with invalidInput, unsent, when the input fails the schema', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const evaluateLampEvidence = vi.fn(fail)
    const ai = createMarshalAi({ evaluateLampEvidence })
    const lampInput = {
      vin: 'V',
      step: 'headlights',
      telemetry: { headlights: 'ON' },
      frames: Array.from({ length: MAX_FRAMES + 1 }, () => 'AAAA'),
      hint: 'headlights=off',
    }
    const result = await ai.evaluateLampEvidence(lampInput)
    expect(result).toEqual({ ...offlineLamp(lampInput), invalidInput: true })
    expect(evaluateLampEvidence).not.toHaveBeenCalled()
    expect(String(warn.mock.calls[0]?.[0])).toMatch(/input rejected by schema/)
    warn.mockRestore()
  })

  it('flags invalidInput when the server function throws a validation error', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const zodError = gateInputSchema.safeParse({}).error
    const ai = createMarshalAi({
      evaluateGatePhotos: async () => {
        throw zodError
      },
    })
    const result = await ai.evaluateGatePhotos(gateInput)
    expect(result).toEqual({ ...offlineGate(gateInput), invalidInput: true })
    warn.mockRestore()
  })
})
