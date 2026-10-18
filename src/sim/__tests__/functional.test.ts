import { describe, expect, it } from 'vitest'
import { FunctionalChecker, stepVerdict, telemetryWorks } from '../flows/functional'
import { ROUTINE_R12 } from '../routines'
import { makeHost, snap, stubAi } from './helpers'

function lampsFor(step: string, faultHeadlights = false) {
  const lamps = { headlights: true, brake_lights: false }
  if (step === 'headlights_off') lamps.headlights = false
  else if (step === 'brake_lights' || step === 'brake_test') lamps.brake_lights = true
  if (faultHeadlights) lamps.headlights = false
  return lamps
}

describe('stepVerdict', () => {
  it('camera-only steps', () => {
    expect(stepVerdict('headlights_on', true, true)).toBe('PASS')
    expect(stepVerdict('headlights_on', false, true)).toBe('FAIL')
  })
  it('disagreement on a both-criteria step is REVIEW', () => {
    expect(stepVerdict('brake_test', true, false)).toBe('REVIEW')
  })
  it('both criteria failing is FAIL', () => {
    expect(stepVerdict('brake_test', false, false)).toBe('FAIL')
  })
})

describe('telemetryWorks', () => {
  it('checks the brake distance only for brake_test', () => {
    expect(telemetryWorks('brake_test', { brakeDistanceM: 15 })).toBe(true)
    expect(telemetryWorks('brake_test', { brakeDistanceM: 30 })).toBe(false)
    expect(telemetryWorks('headlights_on', { lamp: 'headlights' })).toBe(true)
  })
})

describe('FunctionalChecker (offline)', () => {
  it('passes every R-12 step for a clean car', async () => {
    const checker = new FunctionalChecker(makeHost())
    for (const step of ROUTINE_R12) {
      await checker.decideStep(
        snap('V1', '#2f6fed', lampsFor(step.name)),
        step.name,
        step.telemetry,
      )
    }
    expect(checker.result('V1').verdict).toBe('PASS')
  })

  it('fails the dead_lamp car exactly on the headlights step', async () => {
    const checker = new FunctionalChecker(makeHost())
    for (const step of ROUTINE_R12) {
      await checker.decideStep(
        snap('V2', '#2f6fed', lampsFor(step.name, true)),
        step.name,
        step.telemetry,
      )
    }
    const result = checker.result('V2')
    expect(result.verdict).toBe('FAIL')
    expect(result.findings.map((f) => f.kind)).toEqual(['step failed: headlights_on'])
  })

  it('REVIEWs on camera/telemetry disagreement', async () => {
    const ai = stubAi(async (task) => {
      if (task === 'track.decide') return { works: true, confidence: 0.9 }
      throw new Error('unexpected')
    })
    const checker = new FunctionalChecker(makeHost({ ai }))
    const d = await checker.decideStep(
      snap('V3', '#2f6fed', lampsFor('brake_test')),
      'brake_test',
      {
        brakeDistanceM: 30,
      },
    )
    expect(d.verdict).toBe('REVIEW')
  })
})
