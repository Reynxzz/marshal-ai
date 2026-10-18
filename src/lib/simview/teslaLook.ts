// Tesla-style look for the dashboard 3D view: a pale, fogged plant drawn as lane markings and white
// massing, smooth grey cars with soft contact shadows, the followed car in blue with a glowing path
// ribbon ahead of it. Only the dashboard uses it; the camera frames sent to Gemini keep the dressed
// world (scene.ts `dressed`), where the cars' build colours matter. Builds without WebGL (jsdom).

import * as THREE from 'three'
import {
  CAR_LENGTH_M,
  CAR_WHEELBASE_M,
  CAR_WIDTH_M,
  type EngineCar,
} from '#/lib/simulator/actors/car'
import { applyCarlaPose } from '#/lib/simulator/coords'
import { buildCompound, yardSceneryCarPositions } from '#/lib/simulator/world/dress'
import type { LaneGraph } from '#/lib/simulator/world/plantMap'
import { ROAD_WIDTH_M } from '#/lib/simulator/world/roadBuilder'
import type { PlantLayout, PlantMarker } from './plantMap'
import { toThree } from './plantMap'

/** The palette: haze, tarmac, markings, car paint. */
export const TESLA = {
  haze: 0xe6e8ec,
  ground: 0xe3e6ea,
  road: 0xc5cad1,
  lot: 0xd6d9de,
  marking: 0xffffff,
  yellow: 0xe8b62c,
  massing: 0xf3f4f6,
  zone: 0xc9d0da,
  car: 0xc6cad1,
  focus: 0x2f86ff,
  glass: 0x1c1f24,
  tyre: 0x2a2d32,
  shadow: 0x1a2030,
} as const

/** Fog: everything past FAR fades into the haze, as the car's screen does. */
export const TESLA_FOG = { near: 120, far: 520 } as const

/** Path ribbon: width and how far ahead of the followed car it reaches. */
export const RIBBON = { width: 1.7, aheadM: 45 } as const

const LINE_M = 0.14

function basic(colour: number): THREE.MeshLambertMaterial {
  return new THREE.MeshLambertMaterial({ color: colour })
}

/**
 * A flat rectangle on the ground: `length` along the heading, `width` across it.
 * @param yaw - Heading, radians, CARLA frame (0 = +x); (x, y) is the CARLA centre.
 */
function strip(
  parent: THREE.Object3D,
  material: THREE.Material,
  length: number,
  width: number,
  x: number,
  y: number,
  yaw: number,
  height: number,
): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(length, width), material)
  // Flat on the ground (plane +y -> three -z = CARLA -y), then turned to the heading.
  mesh.rotation.set(-Math.PI / 2, 0, -yaw)
  mesh.position.set(x, height, y)
  parent.add(mesh)
  return mesh
}

/**
 * A marked area: optional fill and a line outline, centred on three-frame (x, z), turned by
 * rotationY (three), as plantMap.toThree gives a marker.
 */
function zone(
  parent: THREE.Object3D,
  line: THREE.Material,
  fill: THREE.Material | null,
  length: number,
  width: number,
  x: number,
  z: number,
  rotationY: number,
  open?: 'front',
): void {
  const group = new THREE.Group()
  group.position.set(x, 0, z)
  group.rotation.y = rotationY
  parent.add(group)
  if (fill) strip(group, fill, length, width, 0, 0, 0, 0.015)
  for (const side of [-1, 1]) strip(group, line, length, LINE_M, 0, (side * width) / 2, 0, 0.035)
  strip(group, line, LINE_M, width, -length / 2, 0, 0, 0.035)
  if (open !== 'front') strip(group, line, LINE_M, width, length / 2, 0, 0, 0.035)
}

function isSlotEdge(from: string, to: string): boolean {
  return from.startsWith('yard.Y') || to.startsWith('yard.Y')
}

/**
 * Roads as light tarmac with white edge lines and a yellow double centre line (the plant's lanes
 * carry both directions). Centre lines stop short of each junction so crossings stay clean.
 */
function buildTeslaRoads(graph: LaneGraph): THREE.Group {
  const group = new THREE.Group()
  group.name = 'roads'
  const road = basic(TESLA.road)
  const white = basic(TESLA.marking)
  const yellow = basic(TESLA.yellow)
  const seen = new Set<string>()
  for (const lane of graph.edges) {
    const key = [lane.from, lane.to].sort().join('|')
    if (seen.has(key) || isSlotEdge(lane.from, lane.to)) continue
    seen.add(key)
    const from = graph.points[lane.from]
    const to = graph.points[lane.to]
    const yaw = Math.atan2(to.y - from.y, to.x - from.x)
    const midX = (from.x + to.x) / 2
    const midY = (from.y + to.y) / 2
    strip(group, road, lane.length + ROAD_WIDTH_M, ROAD_WIDTH_M + 1, midX, midY, yaw, 0.01)
    const nx = -Math.sin(yaw)
    const ny = Math.cos(yaw)
    const edge = (ROAD_WIDTH_M + 1) / 2 - 0.25
    for (const side of [-1, 1]) {
      strip(
        group,
        white,
        lane.length,
        LINE_M,
        midX + nx * edge * side,
        midY + ny * edge * side,
        yaw,
        0.02,
      )
    }
    const centre = lane.length - ROAD_WIDTH_M * 1.6
    if (centre > 1) {
      for (const side of [-1, 1]) {
        const off = 0.12 * side
        strip(group, yellow, centre, LINE_M * 0.8, midX + nx * off, midY + ny * off, yaw, 0.025)
      }
    }
  }
  return group
}

function buildMarkerTesla(parent: THREE.Object3D, marker: PlantMarker, mat: TeslaMaterials): void {
  const { position, rotationY } = toThree(marker.x, marker.y, marker.yaw)
  const [x, , z] = position
  switch (marker.kind) {
    case 'gate': {
      // A white arch over the lane, as Tesla draws structures: plain massing, no texture.
      const group = new THREE.Group()
      group.position.set(x, 0, z)
      group.rotation.y = rotationY
      for (const side of [-1, 1]) {
        const post = new THREE.Mesh(new THREE.BoxGeometry(0.5, 4.4, 0.5), mat.massing)
        post.position.set(0, 2.2, side * 3.6)
        group.add(post)
      }
      const beam = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.6, 7.7), mat.massing)
      beam.position.set(0, 4.4, 0)
      group.add(beam)
      parent.add(group)
      break
    }
    case 'track':
      zone(parent, mat.white, mat.zone, 30, 8, x, z, rotationY)
      break
    case 'rework':
      zone(parent, mat.yellow, mat.zone, 8, 6, x, z, rotationY)
      break
    case 'slot':
      zone(parent, mat.white, null, 5.4, 2.7, x, z, rotationY, 'front')
      break
    case 'dock':
      zone(parent, mat.white, null, 22, 12, x, z, rotationY)
      break
    default:
      break
  }
}

type TeslaMaterials = {
  white: THREE.Material
  yellow: THREE.Material
  zone: THREE.Material
  massing: THREE.Material
  lot: THREE.Material
}

/**
 * The plant in the Tesla look: ground, lanes, yard lot with slot outlines, zones, white massing.
 * @param layout - Plant layout (markers, bounds).
 * @param graph - Lane graph the engine drives.
 * @returns Group named `tesla-map`.
 */
export function buildTeslaMap(layout: PlantLayout, graph: LaneGraph): THREE.Group {
  const group = new THREE.Group()
  group.name = 'tesla-map'
  const mat: TeslaMaterials = {
    white: basic(TESLA.marking),
    yellow: basic(TESLA.yellow),
    zone: basic(TESLA.zone),
    massing: new THREE.MeshLambertMaterial({ color: TESLA.massing }),
    lot: basic(TESLA.lot),
  }
  group.add(buildTeslaRoads(graph))

  // The yard is a lot, not a road per slot: one pad under the slots and the scenery row.
  const slots = layout.markers.filter((marker) => marker.kind === 'slot')
  const scenery = yardSceneryCarPositions(graph.points)
  if (slots.length > 0) {
    const xs = [...slots.map((s) => s.x), ...scenery.map((s) => s.x)]
    const ys = [...slots.map((s) => s.y), ...scenery.map((s) => s.y)]
    const minX = Math.min(...xs) - 4
    const maxX = Math.max(...xs) + 4
    const minY = Math.min(...ys) - 6
    const maxY = Math.max(...ys) + 6
    strip(group, mat.lot, maxX - minX, maxY - minY, (minX + maxX) / 2, (minY + maxY) / 2, 0, 0.008)
  }
  for (const marker of layout.markers) buildMarkerTesla(group, marker, mat)

  // Buildings as plain white blocks.
  const compound = buildCompound(graph.points)
  compound.traverse((node: THREE.Object3D) => {
    const mesh = node as THREE.Mesh
    if (mesh.isMesh) mesh.material = mat.massing
  })
  group.add(compound)

  // Parked scenery cars in the yard row: the same grey cars as the plant's own.
  for (const pose of scenery) {
    const car = createTeslaCar()
    applyCarlaPose(car, pose)
    group.add(car)
  }
  return group
}

// --- Car ---------------------------------------------------------------------------------------

function lamp(colour: number): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: 0x30343a,
    emissive: colour,
    emissiveIntensity: 0,
    roughness: 0.4,
  })
}

/** Side profile of the lower body (x along the car, nose at +x; y up), metres. */
function bodyShape(): THREE.Shape {
  const half = CAR_LENGTH_M / 2
  const shape = new THREE.Shape()
  shape.moveTo(-half + 0.1, 0.3)
  shape.lineTo(half - 0.2, 0.3)
  // Low rounded nose, a hood rising to the windshield, a beltline climbing to a high tail.
  shape.quadraticCurveTo(half + 0.05, 0.32, half + 0.02, 0.55)
  shape.quadraticCurveTo(half - 0.03, 0.72, half - 0.45, 0.76)
  shape.quadraticCurveTo(half - 1.25, 0.86, half - 1.5, 0.92)
  shape.lineTo(-half + 0.5, 1.0)
  shape.quadraticCurveTo(-half - 0.01, 1.0, -half + 0.02, 0.72)
  shape.quadraticCurveTo(-half - 0.01, 0.34, -half + 0.1, 0.3)
  return shape
}

/** Side profile of the glass cabin that sits on the beltline. */
function cabinShape(): THREE.Shape {
  const shape = new THREE.Shape()
  shape.moveTo(1.12, 0.89)
  shape.quadraticCurveTo(0.55, 1.34, -0.1, 1.43)
  shape.lineTo(-0.8, 1.43)
  shape.quadraticCurveTo(-1.7, 1.38, -2.02, 0.99)
  shape.lineTo(1.12, 0.89)
  return shape
}

function extrude(shape: THREE.Shape, width: number, bevel: number): THREE.ExtrudeGeometry {
  const geometry = new THREE.ExtrudeGeometry(shape, {
    depth: width - bevel * 2,
    bevelEnabled: true,
    bevelThickness: bevel,
    bevelSize: bevel * 0.8,
    bevelSegments: 4,
    curveSegments: 14,
  })
  geometry.translate(0, 0, -(width - bevel * 2) / 2)
  return geometry
}

/**
 * A Tesla-style car (local +X is the nose), compatible with setCarLamps / setCarFaults /
 * updateCarMesh: lamp materials live on `userData.parts` as the engine car's do.
 * @returns Car group with a soft contact shadow.
 */
export function createTeslaCar(): EngineCar {
  const group = new THREE.Group()
  group.name = 'tesla-car'
  const body = new THREE.MeshStandardMaterial({
    color: TESLA.car,
    roughness: 0.32,
    metalness: 0.15,
  })
  const glass = new THREE.MeshStandardMaterial({
    color: TESLA.glass,
    roughness: 0.15,
    metalness: 0.5,
  })
  const tyre = new THREE.MeshStandardMaterial({ color: TESLA.tyre, roughness: 0.9 })

  group.add(new THREE.Mesh(extrude(bodyShape(), CAR_WIDTH_M, 0.22), body))
  group.add(new THREE.Mesh(extrude(cabinShape(), CAR_WIDTH_M - 0.34, 0.2), glass))
  for (const x of [CAR_WHEELBASE_M / 2, -CAR_WHEELBASE_M / 2]) {
    for (const z of [CAR_WIDTH_M / 2 - 0.2, -(CAR_WIDTH_M / 2 - 0.2)]) {
      const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.34, 0.26, 20), tyre)
      wheel.rotation.x = Math.PI / 2
      wheel.position.set(x, 0.34, z)
      group.add(wheel)
    }
  }

  // Contact shadow: a soft dark ellipse under the car (no shadow maps needed).
  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(1, 32),
    new THREE.MeshBasicMaterial({
      color: TESLA.shadow,
      transparent: true,
      opacity: 0.22,
      depthWrite: false,
    }),
  )
  shadow.name = 'shadow'
  shadow.rotation.x = -Math.PI / 2
  shadow.scale.set(CAR_LENGTH_M * 0.62, CAR_WIDTH_M * 0.75, 1)
  shadow.position.y = 0.03
  group.add(shadow)

  const half = CAR_LENGTH_M / 2
  const headlight = lamp(0xffffff)
  const brake = lamp(0xff2a1f)
  const reverse = lamp(0xffffff)
  const left = lamp(0xffa020)
  const right = lamp(0xffa020)
  const add = (
    material: THREE.Material,
    size: [number, number, number],
    x: number,
    y: number,
    z: number,
  ) => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), material)
    mesh.position.set(x, y, z)
    group.add(mesh)
  }
  for (const side of [1, -1]) {
    add(headlight, [0.08, 0.06, 0.42], half - 0.12, 0.74, side * 0.6)
    add(side === 1 ? right : left, [0.08, 0.06, 0.14], half - 0.18, 0.74, side * 0.86)
    add(side === 1 ? right : left, [0.08, 0.06, 0.14], -half + 0.06, 0.86, side * 0.84)
    add(reverse, [0.06, 0.05, 0.16], -half + 0.03, 0.62, side * 0.45)
  }
  // Full-width tail light bar.
  add(brake, [0.06, 0.06, 1.4], -half + 0.05, 0.86, 0)

  group.userData.parts = {
    body,
    lamps: {
      headlights: [headlight],
      brake_lights: [brake],
      reverse: [reverse],
      indicator_left: [left],
      indicator_right: [right],
    },
    state: {
      headlights: false,
      brake_lights: false,
      reverse: false,
      indicator_left: false,
      indicator_right: false,
    },
    faults: new Set(),
  }
  return group
}

/**
 * Paints a Tesla car as the followed car (blue) or one of the others (grey).
 * @param car - Car from {@link createTeslaCar}.
 * @param focused - Whether the camera follows it.
 */
export function setTeslaFocus(car: EngineCar, focused: boolean): void {
  const body = (car.userData.parts as { body: THREE.MeshStandardMaterial }).body
  body.color.set(focused ? TESLA.focus : TESLA.car)
  body.emissive.set(focused ? TESLA.focus : 0x000000)
  body.emissiveIntensity = focused ? 0.18 : 0
}

// --- Path ribbon -------------------------------------------------------------------------------

/**
 * The followed car's path ahead as a flat blue ribbon, bright at the car and fading out.
 * @returns Mesh to update with {@link updateRibbon}.
 */
export function createRibbon(): THREE.Mesh {
  const material = new THREE.MeshBasicMaterial({
    color: TESLA.focus,
    vertexColors: true,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
  })
  const mesh = new THREE.Mesh(new THREE.BufferGeometry(), material)
  mesh.name = 'ribbon'
  mesh.renderOrder = 1
  mesh.visible = false
  return mesh
}

/**
 * Rebuilds the ribbon along a polyline (CARLA frame, from the car forward).
 * @param ribbon - Mesh from {@link createRibbon}.
 * @param points - Path points; fewer than two hides the ribbon.
 */
export function updateRibbon(
  ribbon: THREE.Mesh,
  points: readonly { x: number; y: number }[],
): void {
  if (points.length < 2) {
    ribbon.visible = false
    return
  }
  let total = 0
  const along = [0]
  for (let i = 1; i < points.length; i++) {
    total += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y)
    along.push(total)
  }
  const half = RIBBON.width / 2
  const positions: number[] = []
  const colours: number[] = []
  const index: number[] = []
  for (let i = 0; i < points.length; i++) {
    const a = points[Math.max(0, i - 1)]
    const b = points[Math.min(points.length - 1, i + 1)]
    const dx = b.x - a.x
    const dy = b.y - a.y
    const len = Math.hypot(dx, dy) || 1
    const nx = -dy / len
    const ny = dx / len
    const fade = 1 - along[i] / (total || 1)
    for (const side of [-1, 1]) {
      positions.push(points[i].x + nx * half * side, 0.06, points[i].y + ny * half * side)
      colours.push(1, 1, 1, 0.75 * fade)
    }
    if (i > 0) {
      const k = i * 2
      index.push(k - 2, k - 1, k, k - 1, k + 1, k)
    }
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colours, 4))
  geometry.setIndex(index)
  ribbon.geometry.dispose()
  ribbon.geometry = geometry
  ribbon.visible = true
}

/**
 * Cuts a path to at most `maxM` metres from its start.
 * @param points - Path points, from the car forward.
 * @param maxM - Length to keep.
 * @returns The first `maxM` metres (the last point interpolated).
 */
export function clipPath(
  points: readonly { x: number; y: number }[],
  maxM: number = RIBBON.aheadM,
): { x: number; y: number }[] {
  const out = points.length > 0 ? [points[0]] : []
  let left = maxM
  for (let i = 1; i < points.length && left > 0; i++) {
    const a = points[i - 1]
    const b = points[i]
    const step = Math.hypot(b.x - a.x, b.y - a.y)
    if (step <= left) {
      out.push(b)
      left -= step
    } else {
      const t = left / step
      out.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t })
      left = 0
    }
  }
  return out
}
