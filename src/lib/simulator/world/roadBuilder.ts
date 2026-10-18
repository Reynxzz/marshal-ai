// Procedural road geometry for the engine scene (TEAM_PLAN M1.3): asphalt strips with edge
// lines and a dashed centre line, plus the lane centre-line polyline sampled every metre (used
// by the driver later). The CARLA YAML's eight curbed roads (r=22 m quarter bends) are not in
// this repo, so legs between named points are straight strips:
// shortcut: straight lane legs; upgrade to curved centre-lines when the map YAML lands.

import * as THREE from 'three'
import { carlaYawToThree } from '../coords'
import type { LaneGraph } from './plantMap'

/** Lane width, metres. One strip covers both directions in the sim. */
export const ROAD_WIDTH_M = 5
const EDGE_WIDTH_M = 0.14
const DASH_LENGTH_M = 3.2
const DASH_PERIOD_M = 7

/** A sampled point on a lane centre-line, CARLA frame. */
export type SampledPoint = { x: number; y: number }

function standard(colour: number, roughness = 0.9, metalness = 0): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({ color: colour, roughness, metalness })
}

function flat(
  parent: THREE.Object3D,
  material: THREE.Material,
  width: number,
  depth: number,
  x: number,
  y: number,
  rotationY: number,
  height = 0.02,
): THREE.Mesh {
  const item = new THREE.Mesh(new THREE.BoxGeometry(width, 0.02, depth), material)
  item.position.set(x, height, y)
  item.rotation.y = rotationY
  return parent.add(item)
}

/**
 * Samples a straight lane centre-line every `stepM` metres, endpoints included.
 * @param from - Start point, CARLA frame.
 * @param to - End point, CARLA frame.
 * @param stepM - Maximum sample spacing, metres.
 * @returns Samples from `from` to `to`; the last sample is exactly `to`.
 */
export function sampleCenterline(
  from: { x: number; y: number },
  to: { x: number; y: number },
  stepM = 1,
): SampledPoint[] {
  const length = Math.hypot(to.x - from.x, to.y - from.y)
  const steps = Math.max(1, Math.ceil(length / stepM))
  const samples: SampledPoint[] = []
  for (let index = 0; index <= steps; index++) {
    const t = index / steps
    samples.push({ x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t })
  }
  return samples
}

/**
 * Builds one flat road strip per lane edge: asphalt, two edge lines and a dashed centre line.
 * @param graph - Lane graph to draw.
 * @returns Group named `roads`.
 */
export function buildRoads(graph: LaneGraph): THREE.Group {
  const group = new THREE.Group()
  group.name = 'roads'
  const road = standard(0x2f3742, 0.95)
  const edge = standard(0xbfc5cd, 0.7)
  const dash = standard(0x9ea5ae, 0.7)
  for (const lane of graph.edges) {
    const from = graph.points[lane.from]
    const to = graph.points[lane.to]
    const length = lane.length
    const yaw = Math.atan2(to.y - from.y, to.x - from.x)
    const rotationY = carlaYawToThree((yaw * 180) / Math.PI)
    const midX = (from.x + to.x) / 2
    const midY = (from.y + to.y) / 2
    flat(group, road, length + ROAD_WIDTH_M, ROAD_WIDTH_M, midX, midY, rotationY)
    const offset = ROAD_WIDTH_M / 2 - EDGE_WIDTH_M - 0.1
    const nx = -Math.sin(yaw) * offset
    const ny = Math.cos(yaw) * offset
    flat(group, edge, length, EDGE_WIDTH_M, midX + nx, midY + ny, rotationY, 0.04)
    flat(group, edge, length, EDGE_WIDTH_M, midX - nx, midY - ny, rotationY, 0.04)
    const dashes = Math.max(1, Math.floor(length / DASH_PERIOD_M))
    for (let index = 0; index < dashes; index++) {
      const t = (index * DASH_PERIOD_M + DASH_LENGTH_M / 2) / length
      flat(
        group,
        dash,
        DASH_LENGTH_M,
        EDGE_WIDTH_M,
        from.x + (to.x - from.x) * t,
        from.y + (to.y - from.y) * t,
        rotationY,
        0.05,
      )
    }
  }
  return group
}
