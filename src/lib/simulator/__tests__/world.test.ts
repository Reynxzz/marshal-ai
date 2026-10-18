import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { POINTS } from '#/lib/marshal/points'
import {
  buildDockTrucks,
  buildFence,
  buildYardCars,
  dockTruckPositions,
  dressPlant,
  FENCE_MARGIN_M,
  fenceBounds,
  fencePostPositions,
  YARD_SCENERY_SPACING_M,
  yardSceneryCarPositions,
} from '../world/dress'
import { addDuskLighting, sunPosition } from '../world/lighting'
import { boundsOf, createLaneGraph } from '../world/plantMap'
import { buildRoads, sampleCenterline } from '../world/roadBuilder'

describe('roadBuilder', () => {
  it('samples a centre line every metre with exact endpoints', () => {
    const samples = sampleCenterline({ x: 0, y: 0 }, { x: 10, y: 0 })
    expect(samples).toHaveLength(11)
    expect(samples[0]).toEqual({ x: 0, y: 0 })
    expect(samples.at(-1)).toEqual({ x: 10, y: 0 })
    for (let index = 1; index < samples.length; index++) {
      const previous = samples[index - 1]
      const step = Math.hypot(samples[index].x - previous.x, samples[index].y - previous.y)
      expect(step).toBeLessThanOrEqual(1 + 1e-9)
    }
  })

  it('keeps short legs within the step and ends exactly at the target', () => {
    const samples = sampleCenterline({ x: 0, y: 0 }, { x: 2.5, y: 0 }, 1)
    expect(samples).toHaveLength(4)
    expect(samples.at(-1)).toEqual({ x: 2.5, y: 0 })
  })

  it('builds one road group with a strip, two edges and dashes per lane', () => {
    const single = createLaneGraph(POINTS, [['line_end', 'buffer_zone_b']])
    expect(buildRoads(single).children).toHaveLength(4)
    const roads = buildRoads(createLaneGraph())
    expect(roads.name).toBe('roads')
    expect(roads.children.length).toBeGreaterThan(createLaneGraph().edges.length * 3)
    for (const child of roads.children) expect(child).toBeInstanceOf(THREE.Mesh)
  })
})

describe('dress', () => {
  const bounds = boundsOf()

  it('spaces fence posts at most 4m apart on the fence rectangle', () => {
    const fence = fenceBounds(bounds)
    const posts = fencePostPositions(fence)
    const sides = [fence.maxX - fence.minX, fence.maxY - fence.minY]
    const expected = 2 * (Math.ceil(sides[0] / 4) + Math.ceil(sides[1] / 4))
    expect(posts).toHaveLength(expected)
    for (const post of posts) {
      const onX = Math.min(Math.abs(post.x - fence.minX), Math.abs(post.x - fence.maxX))
      const onY = Math.min(Math.abs(post.y - fence.minY), Math.abs(post.y - fence.maxY))
      expect(Math.min(onX, onY)).toBeLessThan(1e-6)
    }
    expect(fence.minX).toBeCloseTo(bounds.minX - FENCE_MARGIN_M)
  })

  it('spaces parked scenery cars every 6m behind the yard row', () => {
    const poses = yardSceneryCarPositions()
    expect(poses.length).toBeGreaterThanOrEqual(8)
    expect(poses[0].x).toBeCloseTo(-38.25)
    for (let index = 1; index < poses.length; index++) {
      expect(poses[index].y - poses[index - 1].y).toBeCloseTo(YARD_SCENERY_SPACING_M)
    }
    for (const pose of poses) expect(pose.yaw).toBe(90)
  })

  it('spaces dock trucks every 13m', () => {
    const poses = dockTruckPositions()
    expect(poses).toHaveLength(3)
    expect(poses[1].x - poses[0].x).toBeCloseTo(13)
    expect(poses[2].x - poses[1].x).toBeCloseTo(13)
  })

  it('assembles every dressing group with meshes', () => {
    const plant = dressPlant()
    expect(plant.name).toBe('dress')
    for (const name of ['fence', 'yard-cars', 'trucks', 'compound', 'gate-props', 'rework-props']) {
      const group = plant.getObjectByName(name)
      expect(group, name).toBeDefined()
      expect(group.children.length, name).toBeGreaterThan(0)
    }
  })

  it('builds fence and yard cars from the named points', () => {
    expect(buildFence(bounds).children.length).toBeGreaterThan(0)
    expect(buildYardCars().children).toHaveLength(yardSceneryCarPositions().length)
    expect(buildDockTrucks().children).toHaveLength(3)
  })
})

describe('lighting', () => {
  it('places the sun 15 degrees above the horizon by default', () => {
    const position = sunPosition({ x: 0, y: 0 })
    const horizontal = Math.hypot(position[0], position[2])
    const elevation = (Math.atan2(position[1], horizontal) * 180) / Math.PI
    expect(elevation).toBeCloseTo(15)
    expect(position[1]).toBeLessThan(horizontal)
  })

  it('adds the sun and an ambient fill to a scene', () => {
    const scene = new THREE.Scene()
    const sun = addDuskLighting(scene, { x: 0, y: 0 })
    expect(scene.children).toContain(sun)
    expect(scene.children).toHaveLength(2)
  })
})
