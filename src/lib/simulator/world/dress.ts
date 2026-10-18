// Plant dressing (TEAM_PLAN M1.5): perimeter fence, parked yard cars, dock trucks, storage
// compound, gate boards/cones/warning sign and rework-bay props, all positioned from the named
// points. The CARLA YAML's road-7 / road-5 rows are not in this repo, so the rows follow the
// yard slots and the dock that do exist:
// shortcut: scenery rows are derived from named points, not the road table; upgrade with the YAML.

import * as THREE from 'three'
import { type NamedPoint, POINTS, type PointName, YARD_SLOTS } from '#/lib/marshal/points'
import { createCarMesh, updateCarMesh } from '../actors/car'
import { applyCarlaPose, type CarlaPose } from '../coords'
import {
  boundsOf,
  createLaneGraph,
  type LaneGraph,
  LOADING_DOCK_POSE,
  type PlantBounds,
} from './plantMap'

/** Fence post spacing, metres (TEAM_PLAN M1.5). */
export const FENCE_SPACING_M = 4
/** Parked yard scenery spacing, metres. */
export const YARD_SCENERY_SPACING_M = 6
/** Loading truck spacing, metres. */
export const TRUCK_SPACING_M = 13
/** Fence distance from the named-point bounds, metres. */
export const FENCE_MARGIN_M = 24

const YARD_CAR_COLOURS: readonly number[] = [0x9aa1ab, 0x6b7280, 0x4f6b8f, 0xb0b6bf, 0x7d8590]

/** A ground position, CARLA frame. */
export type GroundPos = { x: number; y: number }

function standard(colour: number, roughness = 0.9, metalness = 0): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({ color: colour, roughness, metalness })
}

function box(
  parent: THREE.Object3D,
  material: THREE.Material,
  width: number,
  height: number,
  depth: number,
  x: number,
  y: number,
  z: number,
  rotationY = 0,
): THREE.Mesh {
  const item = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), material)
  item.position.set(x, y, z)
  item.rotation.y = rotationY
  parent.add(item)
  return item
}

function cylinder(
  parent: THREE.Object3D,
  material: THREE.Material,
  radius: number,
  height: number,
  x: number,
  y: number,
  z: number,
): THREE.Mesh {
  const item = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, height, 10), material)
  item.position.set(x, y, z)
  parent.add(item)
  return item
}

/**
 * Fence rectangle around the plant points.
 * @param bounds - Named-point bounds.
 * @param marginM - Distance outside the bounds, metres.
 * @returns Expanded bounds.
 */
export function fenceBounds(bounds: PlantBounds, marginM = FENCE_MARGIN_M): PlantBounds {
  return {
    minX: bounds.minX - marginM,
    maxX: bounds.maxX + marginM,
    minY: bounds.minY - marginM,
    maxY: bounds.maxY + marginM,
  }
}

function fenceSides(bounds: PlantBounds): Array<readonly [number, number, number, number]> {
  return [
    [bounds.minX, bounds.minY, bounds.maxX, bounds.minY],
    [bounds.maxX, bounds.minY, bounds.maxX, bounds.maxY],
    [bounds.maxX, bounds.maxY, bounds.minX, bounds.maxY],
    [bounds.minX, bounds.maxY, bounds.minX, bounds.minY],
  ]
}

/**
 * Fence post positions, at most `spacingM` apart, along the fence rectangle.
 * @param bounds - Fence rectangle.
 * @param spacingM - Maximum post spacing, metres.
 * @returns Post positions in walk order.
 */
export function fencePostPositions(bounds: PlantBounds, spacingM = FENCE_SPACING_M): GroundPos[] {
  const posts: GroundPos[] = []
  for (const [ax, ay, bx, by] of fenceSides(bounds)) {
    const length = Math.hypot(bx - ax, by - ay)
    const count = Math.max(1, Math.ceil(length / spacingM))
    for (let index = 0; index < count; index++) {
      const t = index / count
      posts.push({ x: ax + (bx - ax) * t, y: ay + (by - ay) * t })
    }
  }
  return posts
}

/**
 * Static scenery cars behind the yard row, one every `spacingM` metres.
 * @param points - Named points (defaults to the Marshal contract table).
 * @param spacingM - Spacing along the row, metres.
 * @returns Car poses, nose along +y like the slots.
 */
export function yardSceneryCarPositions(
  points: Record<PointName, NamedPoint> = POINTS,
  spacingM = YARD_SCENERY_SPACING_M,
): CarlaPose[] {
  const slots = YARD_SLOTS.map((slot) => points[slot]).filter(
    (point): point is NamedPoint => point !== undefined,
  )
  if (slots.length === 0) return []
  const x = Math.min(...slots.map((point) => point.x)) - 7.5
  const minY = Math.min(...slots.map((point) => point.y)) - spacingM
  const maxY = Math.max(...slots.map((point) => point.y)) + spacingM
  const poses: CarlaPose[] = []
  for (let y = minY; y <= maxY + 1e-9; y += spacingM) poses.push({ x, y, yaw: 90 })
  return poses
}

/**
 * Loading trucks along the dock, one every `spacingM` metres.
 * @param spacingM - Spacing between truck centres, metres.
 * @returns Truck poses, nose along -x (backed against the dock).
 */
export function dockTruckPositions(spacingM = TRUCK_SPACING_M): CarlaPose[] {
  return [-1, 0, 1].map((offset) => ({
    x: LOADING_DOCK_POSE.x + offset * spacingM,
    y: LOADING_DOCK_POSE.y,
    yaw: 180,
  }))
}

/**
 * Perimeter fence: posts with two rails.
 * @param bounds - Named-point bounds (the fence sits outside them).
 * @param spacingM - Max post spacing, metres.
 * @returns Group named `fence`.
 */
export function buildFence(bounds: PlantBounds, spacingM = FENCE_SPACING_M): THREE.Group {
  const group = new THREE.Group()
  group.name = 'fence'
  const fence = fenceBounds(bounds)
  const post = standard(0x8a929c, 0.5, 0.4)
  const rail = standard(0x767e88, 0.6, 0.3)
  for (const position of fencePostPositions(fence, spacingM)) {
    cylinder(group, post, 0.07, 1.9, position.x, 0.95, position.y)
  }
  for (const [ax, ay, bx, by] of fenceSides(fence)) {
    const length = Math.hypot(bx - ax, by - ay)
    const rotationY = -Math.atan2(by - ay, bx - ax)
    box(group, rail, length, 0.06, 0.06, (ax + bx) / 2, 1.5, (ay + by) / 2, rotationY)
    box(group, rail, length, 0.06, 0.06, (ax + bx) / 2, 0.9, (ay + by) / 2, rotationY)
  }
  return group
}

/**
 * Parked scenery cars behind the yard row.
 * @param points - Named points (defaults to the Marshal contract table).
 * @returns Group named `yard-cars`.
 */
export function buildYardCars(points: Record<PointName, NamedPoint> = POINTS): THREE.Group {
  const group = new THREE.Group()
  group.name = 'yard-cars'
  yardSceneryCarPositions(points).forEach((pose, index) => {
    const car = createCarMesh(YARD_CAR_COLOURS[index % YARD_CAR_COLOURS.length])
    updateCarMesh(car, pose)
    group.add(car)
  })
  return group
}

/**
 * Loading trucks along the dock.
 * @param spacingM - Spacing between truck centres, metres.
 * @returns Group named `trucks`.
 */
export function buildDockTrucks(spacingM = TRUCK_SPACING_M): THREE.Group {
  const group = new THREE.Group()
  group.name = 'trucks'
  const body = standard(0xd7d7d7, 0.6)
  const cab = standard(0x4f6b8f, 0.55, 0.2)
  const tire = standard(0x141619, 0.95)
  for (const pose of dockTruckPositions(spacingM)) {
    const truck = new THREE.Group()
    truck.name = 'truck'
    box(truck, body, 7.5, 2.6, 2.5, -0.8, 1.85, 0)
    box(truck, cab, 2.2, 2, 2.4, 3.2, 1.4, 0)
    for (const z of [1.05, -1.05]) {
      for (const x of [-3.5, 1.5, 3.5]) {
        const wheel = cylinder(truck, tire, 0.5, 0.3, x, 0.5, z)
        wheel.rotation.x = Math.PI / 2
      }
    }
    applyCarlaPose(truck, pose)
    group.add(truck)
  }
  return group
}

/**
 * Storage compound: two rows of crates inside the loop.
 * @param points - Named points (defaults to the Marshal contract table).
 * @returns Group named `compound`.
 */
export function buildCompound(points: Record<PointName, NamedPoint> = POINTS): THREE.Group {
  const group = new THREE.Group()
  group.name = 'compound'
  const crate = standard(0x8a6a44, 0.9)
  const base = { x: points.line_end.x + 45, y: points.line_end.y - 30 }
  for (let row = 0; row < 2; row++) {
    for (let column = 0; column < 5; column++) {
      box(group, crate, 1.2, 1.2, 1.2, base.x + column * 1.6, 0.62, base.y - row * 1.8)
    }
  }
  return group
}

/**
 * Gate dressing: calibration boards, cones and a warning board.
 * @param points - Named points (defaults to the Marshal contract table).
 * @returns Group named `gate-props`.
 */
export function buildGateProps(points: Record<PointName, NamedPoint> = POINTS): THREE.Group {
  const group = new THREE.Group()
  group.name = 'gate-props'
  const gate = points.inspection_gate
  const board = standard(0xd9d5cb, 0.5, 0.3)
  const post = standard(0x8a929c, 0.45, 0.5)
  const cone = standard(0xff7a1a, 0.6)
  const sign = standard(0xf28c28, 0.6)
  for (const side of [1, -1]) {
    const x = gate.x + 6
    const y = gate.y + side * 5.5
    box(group, board, 2.4, 1.6, 0.12, x, 1.6, y)
    cylinder(group, post, 0.08, 1.7, x - 0.9, 0.85, y)
    cylinder(group, post, 0.08, 1.7, x + 0.9, 0.85, y)
  }
  for (let index = 0; index < 4; index++) {
    const item = new THREE.Mesh(new THREE.ConeGeometry(0.22, 0.7, 12), cone)
    item.position.set(gate.x + 12 + index * 4, 0.35, gate.y - 4)
    group.add(item)
  }
  box(group, sign, 1.6, 1.1, 0.1, gate.x - 6, 1.3, gate.y + 4.5)
  return group
}

/**
 * Rework-bay props: barrels, crate, iron planks and a defect sign.
 * @param points - Named points (defaults to the Marshal contract table).
 * @returns Group named `rework-props`.
 */
export function buildReworkProps(points: Record<PointName, NamedPoint> = POINTS): THREE.Group {
  const group = new THREE.Group()
  group.name = 'rework-props'
  const bay = points['rework.electrical']
  const barrel = standard(0x2f6fed, 0.6)
  const crate = standard(0x8a6a44, 0.9)
  const plank = standard(0x6d5b45, 0.9)
  const sign = standard(0xf28c28, 0.6)
  cylinder(group, barrel, 0.3, 0.9, bay.x - 3, 0.45, bay.y - 2.4)
  cylinder(group, barrel, 0.3, 0.9, bay.x - 3, 0.45, bay.y + 2.4)
  box(group, crate, 1.2, 1, 1.2, bay.x - 5, 0.5, bay.y)
  box(group, plank, 2.4, 0.12, 0.4, bay.x + 2, 0.06, bay.y + 3, 0.4)
  box(group, plank, 2.4, 0.12, 0.4, bay.x + 2.4, 0.2, bay.y + 3.2, 0.4)
  box(group, sign, 1.2, 0.9, 0.1, bay.x + 4, 1.1, bay.y, -Math.PI / 2)
  return group
}

/**
 * Assembles all dressing groups.
 * @param graph - Lane graph (defaults to the Marshal contract map).
 * @returns Group named `dress` with one named subgroup per category.
 */
export function dressPlant(graph: LaneGraph = createLaneGraph()): THREE.Group {
  const group = new THREE.Group()
  group.name = 'dress'
  const bounds = boundsOf(graph.points)
  group.add(buildFence(bounds))
  group.add(buildYardCars(graph.points))
  group.add(buildDockTrucks())
  group.add(buildCompound(graph.points))
  group.add(buildGateProps(graph.points))
  group.add(buildReworkProps(graph.points))
  return group
}
