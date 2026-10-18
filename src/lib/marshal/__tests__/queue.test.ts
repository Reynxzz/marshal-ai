// Queue discipline (TEAM_PLAN M4.1): a car keeps QUEUE_GAP_M (centre to centre: a 6 m standing gap
// plus a car length) behind the car ahead while it holds the lane (active mission, or standing
// between missions), and resumes when the queue moves. Cars spawn 9 m apart (TEAM_PLAN M4.5),
// closer than that gap, so a follower stays where it spawned until the leader pulls away.

import { describe, expect, it } from 'vitest'
import { POINTS } from '../points'
import { IDLE_QUEUE_S, QUEUE_GAP_M, ScriptedDrivingSystem } from '../testing/scriptedSystem'

const SPAWN_GAP_M = 9

function go(ds: ScriptedDrivingSystem, vin: string, waypoints: string[]): void {
  ds.send({ op: 'mission.go', mission_id: `M-${vin}`, vin, waypoints })
}

function gap(ds: ScriptedDrivingSystem): number {
  return ds.getPositionXY('VIN-A').x - ds.getPositionXY('VIN-B').x
}

describe('scripted queue discipline', () => {
  it('waits behind a held car and resumes without overtaking', () => {
    const ds = new ScriptedDrivingSystem()
    ds.spawn('VIN-A', { at: 'line_end', colour: [0, 0, 1] })
    ds.spawn('VIN-B', { at: 'line_end', colour: [0, 0, 1], behind_m: SPAWN_GAP_M })
    const waypoints = ['buffer_zone_b', 'inspection_gate']
    go(ds, 'VIN-A', waypoints)
    go(ds, 'VIN-B', waypoints)
    ds.send({ op: 'mission.hold', vin: 'VIN-A', duration_s: 5, reason: 'test hold' })

    // While A is held at the line end, B (already inside the queue gap) waits where it spawned.
    for (let i = 0; i < 6; i++) ds.tick(0.5)
    expect(ds.getPositionXY('VIN-A').x).toBeCloseTo(POINTS.line_end.x, 5)
    expect(gap(ds)).toBeCloseTo(SPAWN_GAP_M, 5)
    const settled = ds.getPositionXY('VIN-B').x
    ds.tick(0.5)
    expect(ds.getPositionXY('VIN-B').x).toBe(settled)

    // The hold expires: A drives off and B follows, never closer than the gap.
    let sawBMove = false
    for (let i = 0; i < 200 && ds.missionOf('VIN-A'); i++) {
      const before = ds.getPositionXY('VIN-B').x
      ds.tick(0.5)
      expect(gap(ds)).toBeGreaterThanOrEqual(Math.min(SPAWN_GAP_M, QUEUE_GAP_M) - 1e-9)
      if (ds.getPositionXY('VIN-B').x > before + 1e-9) sawBMove = true
    }
    expect(sawBMove).toBe(true)

    // A arrived at the gate and stands in the lane: B eases into its queue spot and stops there
    // (it does not pile on).
    expect(ds.getPositionXY('VIN-A').x).toBeCloseTo(POINTS.inspection_gate.x, 5)
    // Settle for less than IDLE_QUEUE_S (after that a car queued behind a car parked at its own
    // destination counts as arrived in its queue spot).
    for (let t = 0; t < IDLE_QUEUE_S / 2; t += 0.5) ds.tick(0.5)
    const queued = ds.getPositionXY('VIN-B').x
    ds.tick(0.5)
    ds.tick(0.5)
    expect(ds.getPositionXY('VIN-B').x).toBe(queued)
    expect(gap(ds)).toBeGreaterThanOrEqual(QUEUE_GAP_M - 1e-9)
    expect(gap(ds)).toBeLessThan(QUEUE_GAP_M + 0.5)

    // The gate flow sends A on: B finishes its own mission.
    go(ds, 'VIN-A', ['test_track'])
    for (let i = 0; i < 100 && ds.missionOf('VIN-B'); i++) ds.tick(0.5)
    expect(ds.missionOf('VIN-B')).toBeNull()
    expect(ds.getPositionXY('VIN-B').x).toBeCloseTo(POINTS.inspection_gate.x, 5)
  })
})
