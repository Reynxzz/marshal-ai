import { describe, expect, it } from 'vitest'
import {
  offLamps,
  R12_BLINK_PERIOD_S,
  R12_BRAKE_TEST_KMH,
  R12_REVERSE_M,
  R12_ROUTINE,
  R12_STEPS,
  r12Steps,
} from '../actors/routine'

describe('R-12 routine', () => {
  it('defines the six TEAM_PLAN steps in order', () => {
    expect(R12_ROUTINE).toBe('R-12')
    expect(R12_STEPS.map((step) => step.step)).toEqual([1, 2, 3, 4, 5, 6])
    expect(R12_STEPS.map((step) => step.name)).toEqual([
      'headlights',
      'indicators',
      'brake_lights',
      'reverse',
      'brake_test',
      'full_lock',
    ])
    expect(R12_STEPS[0]).toMatchObject({ duration_s: 8, telemetry: { headlights: 'ON' } })
    expect(R12_STEPS[1].duration_s).toBe(10)
    expect(R12_STEPS[2].telemetry).toEqual({ brake_signal: true, brake_lamps: 'ON' })
    expect(R12_STEPS[3].telemetry).toMatchObject({ gear: 'R', distance_m: R12_REVERSE_M })
    expect(R12_STEPS[4].telemetry).toMatchObject({ start_speed_kmh: R12_BRAKE_TEST_KMH })
    expect(R12_STEPS[5].telemetry).toMatchObject({ radius_left_m: 5.4, radius_right_m: 5.5 })
    expect(R12_BLINK_PERIOD_S).toBe(1.2)
  })

  it('turns exactly the step lamp on and everything off for full_lock', () => {
    expect(R12_STEPS[0].lamps.headlights).toBe(true)
    expect(R12_STEPS[0].lamps.brake_lights).toBe(false)
    expect(R12_STEPS[2].lamps.brake_lights).toBe(true)
    expect(R12_STEPS[5].lamps).toEqual(offLamps())
  })

  it('filters to a re-check subset in table order', () => {
    expect(r12Steps([3, 1]).map((step) => step.step)).toEqual([1, 3])
    expect(r12Steps()).toHaveLength(6)
    expect(r12Steps([])).toEqual([])
  })
})
