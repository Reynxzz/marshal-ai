import { describe, expect, it } from 'vitest'
import { POINTS, YARD_SLOTS } from '#/lib/marshal/points'
import { followCamera, overviewCamera, stepCamera } from '../camera'
import { buildPlantLayout, PLANT_SEGMENTS, toThree } from '../plantMap'

describe('buildPlantLayout', () => {
  const layout = buildPlantLayout()

  it('draws a road for every leg between named points', () => {
    for (const [from, to] of PLANT_SEGMENTS) {
      expect(Object.hasOwn(POINTS, from), from).toBe(true)
      expect(Object.hasOwn(POINTS, to), to).toBe(true)
    }
    expect(layout.roads).toHaveLength(PLANT_SEGMENTS.length)
    const gateToTrack = layout.roads.find(
      (road) => road.from === 'inspection_gate' && road.to === 'test_track',
    )
    expect(gateToTrack).toMatchObject({ ax: 32, bx: 62, length: 30, yaw: 0, cx: 47 })
    const toLaneC = layout.roads.find((road) => road.to === 'lane_c')
    expect(toLaneC?.length).toBeCloseTo(Math.hypot(17.25, 55.25))
  })

  it('labels the gate, track, rework bay, yard slots and dock', () => {
    const byName = Object.fromEntries(layout.markers.map((marker) => [marker.name, marker]))
    expect(byName.inspection_gate).toMatchObject({ kind: 'gate', label: 'Inspection gate' })
    expect(byName.test_track.kind).toBe('track')
    expect(byName['rework.electrical']).toMatchObject({ kind: 'rework', x: 150.75 })
    expect(byName.dock.kind).toBe('dock')
    for (const slot of YARD_SLOTS) {
      expect(byName[slot]).toMatchObject({ kind: 'slot', label: slot.replace('yard.', '') })
    }
  })

  it('bounds every named point', () => {
    for (const point of Object.values(POINTS)) {
      expect(point.x).toBeGreaterThanOrEqual(layout.bounds.minX)
      expect(point.x).toBeLessThanOrEqual(layout.bounds.maxX)
      expect(point.y).toBeGreaterThanOrEqual(layout.bounds.minY)
      expect(point.y).toBeLessThanOrEqual(layout.bounds.maxY)
    }
  })

  it('skips legs to unknown points', () => {
    const custom = buildPlantLayout({ a: { x: 0, y: 0, yaw: 0 } }, [['a', 'missing']])
    expect(custom.roads).toEqual([])
  })
})

describe('toThree', () => {
  it('maps CARLA (x, y, yaw) to three (x, h, y) and rotation.y = -yaw', () => {
    expect(toThree(10, -20, 90, 1)).toEqual({ position: [10, 1, -20], rotationY: -Math.PI / 2 })
  })
})

describe('camera', () => {
  it('looks down on the plant centre for the overview', () => {
    const layout = buildPlantLayout()
    const view = overviewCamera(layout)
    expect(view.target).toEqual([layout.center.x, 0, layout.center.y])
    expect(view.position[1]).toBeGreaterThan(100)
  })

  it('follows from behind the car', () => {
    const east = followCamera({ x: 50, y: 5, yaw: 0 })
    expect(east.target).toEqual([50, 1, 5])
    expect(east.position[0]).toBeLessThan(50)
    const north = followCamera({ x: 0, y: 0, yaw: 90 })
    expect(north.position[2]).toBeLessThan(0)
  })

  it('eases towards the goal and stays put for dt = 0', () => {
    const from = {
      position: [0, 0, 0] as [number, number, number],
      target: [0, 0, 0] as [number, number, number],
    }
    const goal = {
      position: [10, 10, 10] as [number, number, number],
      target: [1, 1, 1] as [number, number, number],
    }
    expect(stepCamera(from, goal, 0)).toEqual(from)
    const half = stepCamera(from, goal, 0.25)
    expect(half.position[0]).toBeGreaterThan(0)
    expect(half.position[0]).toBeLessThan(10)
    let pose = from
    for (let i = 0; i < 120; i++) pose = stepCamera(pose, goal, 1 / 30)
    expect(pose.position[0]).toBeCloseTo(10, 1)
    expect(pose.target[2]).toBeCloseTo(1, 1)
  })
})
