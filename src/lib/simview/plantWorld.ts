// The plant as a place: CARLA's road loop (simulator/world/plantRoads.ts) drawn lane by lane, and
// the site around it - assembly hall, paint shop, inspection tunnel, test-track canopy, rework
// hall, finishing hall, logistics warehouse, yard lot, an office and staff car park inside the
// loop, lawns, trees, light poles and crossings. Two styles from one layout: 'tesla' (pale massing
// for the dashboard) and 'real' (materials and textures for the camera frames Gemini judges).
// CARLA frame (x, y metres) mapped to three as (x, height, y). Builds without WebGL.

import * as THREE from 'three'
import {
  at,
  LANE,
  LANE_C,
  LOOP,
  type Pose,
  type Pt,
  STAGING,
  sampleChain,
} from '#/lib/simulator/world/plantRoads'
import { grainTexture, shadowed } from './realLook'
import { createTeslaCar, TESLA } from './teslaLook'

export type WorldStyle = 'tesla' | 'real'

/** Site extent (the perimeter fence), CARLA frame. */
export const SITE_BOUNDS = { minX: -128, maxX: 196, minY: -162, maxY: 60 } as const

type Palette = {
  road: THREE.Material
  shoulder: THREE.Material
  white: THREE.Material
  yellow: THREE.Material
  lot: THREE.Material
  grass: THREE.Material
  wall: THREE.Material
  band: THREE.Material
  glass: THREE.Material
  roof: THREE.Material
  door: THREE.Material
  frame: THREE.Material
  steel: THREE.Material
  canopy: THREE.Material
  trunk: THREE.Material
  leaves: THREE.Material
  lamp: THREE.Material
}

function palette(style: WorldStyle): Palette {
  const flat = (colour: number) =>
    new THREE.MeshLambertMaterial({ color: colour, side: THREE.DoubleSide })
  if (style === 'tesla') {
    const massing = new THREE.MeshLambertMaterial({ color: TESLA.massing })
    const soft = new THREE.MeshLambertMaterial({ color: 0xe1e4e8 })
    return {
      road: flat(TESLA.road),
      shoulder: flat(0xd3d7dc),
      white: flat(TESLA.marking),
      yellow: flat(TESLA.yellow),
      lot: flat(0xd5d9de),
      grass: flat(0xdbe1dc),
      wall: massing,
      band: massing,
      glass: soft,
      roof: massing,
      door: soft,
      frame: massing,
      steel: soft,
      canopy: massing,
      trunk: soft,
      leaves: new THREE.MeshLambertMaterial({ color: 0xe9ecef }),
      lamp: massing,
    }
  }
  const std = (colour: number, roughness = 0.8, metalness = 0) =>
    new THREE.MeshStandardMaterial({ color: colour, roughness, metalness, side: THREE.DoubleSide })
  const textured = (base: string, grain: number, fallback: number, roughness: number) => {
    const map = grainTexture(base, grain)
    if (!map) return std(fallback, roughness)
    map.repeat.set(1, 1)
    return new THREE.MeshStandardMaterial({ map, roughness, side: THREE.DoubleSide })
  }
  return {
    road: textured('#46494e', 0.22, 0x46494e, 0.92),
    shoulder: textured('#5a5d61', 0.2, 0x5a5d61, 0.92),
    white: std(0xeeeeea, 0.6),
    yellow: std(0xe3b02c, 0.6),
    lot: textured('#55585c', 0.2, 0x55585c, 0.92),
    grass: textured('#5f7f3d', 0.3, 0x5f7f3d, 1),
    wall: std(0xc9ccd0, 0.75),
    band: std(0x1f5fbf, 0.5),
    glass: std(0x23313f, 0.15, 0.6),
    roof: std(0x868b91, 0.85),
    door: std(0x3b4047, 0.6, 0.3),
    frame: std(0xe07b1a, 0.55),
    steel: std(0x9aa0a8, 0.4, 0.7),
    canopy: std(0xb8bdc4, 0.5, 0.5),
    trunk: std(0x6b4b2e, 0.9),
    leaves: std(0x4b7a34, 0.85),
    lamp: new THREE.MeshStandardMaterial({
      color: 0xf6f3e8,
      emissive: 0xfff6dc,
      emissiveIntensity: 0.4,
    }),
  }
}

// --- Ribbons along the roads -------------------------------------------------------------------

/** One quad strip along a sampled reference line, from offset d0 to d1, at height y. */
function ribbonGeometry(
  line: readonly Pose[],
  ranges: readonly [number, number][],
  d0: number,
  d1: number,
  y: number,
) {
  const positions: number[] = []
  const uvs: number[] = []
  const index: number[] = []
  const along = [0]
  for (let i = 1; i < line.length; i++) {
    along.push(along[i - 1] + Math.hypot(line[i].x - line[i - 1].x, line[i].y - line[i - 1].y))
  }
  const sample = (s: number): Pose => {
    let i = 1
    while (i < line.length - 1 && along[i] < s) i++
    const t = (s - along[i - 1]) / Math.max(1e-9, along[i] - along[i - 1])
    const a = line[i - 1]
    const b = line[i]
    let dh = b.heading - a.heading
    while (dh > Math.PI) dh -= Math.PI * 2
    while (dh < -Math.PI) dh += Math.PI * 2
    return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, heading: a.heading + dh * t }
  }
  for (const [s0, s1] of ranges) {
    const steps = Math.max(1, Math.ceil(s1 - s0))
    const base = positions.length / 3
    for (let k = 0; k <= steps; k++) {
      const s = s0 + ((s1 - s0) * k) / steps
      const pose = sample(s)
      for (const d of [d0, d1]) {
        const p = at({ pose: () => pose }, 0, d)
        positions.push(p.x, y, p.y)
        uvs.push(s / 6, d / 6)
      }
      if (k > 0) {
        const v = base + k * 2
        index.push(v - 2, v - 1, v, v - 1, v + 1, v)
      }
    }
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2))
  geometry.setIndex(index)
  geometry.computeVertexNormals()
  return { geometry, length: along[along.length - 1] }
}

function band(
  parent: THREE.Object3D,
  line: readonly Pose[],
  d0: number,
  d1: number,
  material: THREE.Material,
  y: number,
) {
  const total = ribbonGeometry(line, [], 0, 0, 0).length
  const { geometry } = ribbonGeometry(line, [[0, total]], d0, d1, y)
  const mesh = new THREE.Mesh(geometry, material)
  mesh.receiveShadow = true
  parent.add(mesh)
  return mesh
}

function dashed(
  parent: THREE.Object3D,
  line: readonly Pose[],
  d: number,
  material: THREE.Material,
  y: number,
) {
  const total = ribbonGeometry(line, [], 0, 0, 0).length
  const ranges: [number, number][] = []
  for (let s = 1.5; s + 3 < total; s += 7.5) ranges.push([s, s + 3])
  const { geometry } = ribbonGeometry(line, ranges, d - 0.07, d + 0.07, y)
  parent.add(new THREE.Mesh(geometry, material))
}

function solid(
  parent: THREE.Object3D,
  line: readonly Pose[],
  d: number,
  material: THREE.Material,
  y: number,
  w = 0.14,
) {
  band(parent, line, d - w / 2, d + w / 2, material, y)
}

/** Zebra crossing across the carriageway at distance s along a line. */
function zebra(
  parent: THREE.Object3D,
  path: { pose(s: number): Pose },
  s: number,
  material: THREE.Material,
) {
  const pose = path.pose(s)
  for (let k = 0; k < 7; k++) {
    const d = 0.5 + k * 0.95
    const p = at({ pose: () => pose }, 0, d)
    const stripe = new THREE.Mesh(new THREE.PlaneGeometry(3, 0.5), material)
    stripe.rotation.set(-Math.PI / 2, 0, -pose.heading)
    stripe.position.set(p.x, 0.035, p.y)
    parent.add(stripe)
  }
}

/**
 * CARLA's roads: the loop (two lanes and the parking shoulder), lane C (two lanes) and the
 * staging road out of the assembly hall, with their markings: a yellow left edge (one-way), a
 * dashed lane line, a white right edge, the shoulder edge.
 * @param style - Dashboard or camera look.
 * @returns Group named `roads`.
 */
export function buildRoadNetwork(style: WorldStyle): THREE.Group {
  const mat = palette(style)
  const group = new THREE.Group()
  group.name = 'roads'
  const loop = sampleChain(LOOP)
  const laneC = sampleChain(LANE_C)
  const staging: Pose[] = [
    { x: STAGING.fromX, y: 0, heading: 0 },
    { x: STAGING.toX, y: 0, heading: 0 },
  ]
  band(group, loop, 0, LANE.edge, mat.road, 0.01)
  band(group, loop, LANE.edge, LANE.shoulderEdge, mat.shoulder, 0.012)
  band(group, laneC, -0.2, LANE.edge, mat.road, 0.014)
  band(group, staging, LANE.outer - 3.5, LANE.outer + 3.5, mat.road, 0.011)
  for (const line of [loop, laneC]) {
    solid(group, line, 0.2, mat.yellow, 0.03)
    dashed(group, line, LANE.edge / 2, mat.white, 0.03)
    solid(group, line, LANE.edge - 0.15, mat.white, 0.03)
  }
  solid(group, loop, LANE.shoulderEdge - 0.12, mat.white, 0.03, 0.1)
  solid(group, staging, LANE.outer - 3.35, mat.white, 0.03)
  solid(group, staging, LANE.outer + 3.35, mat.white, 0.03)
  // Crossings where people walk between the halls: before the report point, on the finishing
  // straight by the office, and on the yard road by the warehouse.
  zebra(group, LOOP, 84, mat.white)
  zebra(group, LOOP, 310, mat.white)
  zebra(group, LOOP, 452, mat.white)
  if (style === 'real') shadowed(group)
  return group
}

// --- Site --------------------------------------------------------------------------------------

/** A box by its CARLA-frame extent and height (bottom on the ground unless `y0`). */
function block(
  parent: THREE.Object3D,
  material: THREE.Material,
  minX: number,
  maxX: number,
  minY: number,
  maxY: number,
  height: number,
  y0 = 0,
): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(maxX - minX, height, maxY - minY), material)
  mesh.position.set((minX + maxX) / 2, y0 + height / 2, (minY + maxY) / 2)
  parent.add(mesh)
  return mesh
}

function pad(
  parent: THREE.Object3D,
  material: THREE.Material,
  minX: number,
  maxX: number,
  minY: number,
  maxY: number,
  y = 0.006,
) {
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(maxX - minX, maxY - minY), material)
  mesh.rotation.x = -Math.PI / 2
  mesh.position.set((minX + maxX) / 2, y, (minY + maxY) / 2)
  mesh.receiveShadow = true
  const map = (material as THREE.MeshStandardMaterial).map
  if (map) {
    const tiled = map.clone()
    tiled.needsUpdate = true
    tiled.repeat.set((maxX - minX) / 6, (maxY - minY) / 6)
    mesh.material = (material as THREE.MeshStandardMaterial).clone()
    mesh.material.map = tiled
  }
  parent.add(mesh)
  return mesh
}

type Facade = 'n' | 's' | 'e' | 'w'

/**
 * An industrial hall: walls, a coloured band under the roof line, a ribbon of windows, rooftop
 * units, and roller doors on one side.
 */
function hall(
  parent: THREE.Object3D,
  mat: Palette,
  box: { minX: number; maxX: number; minY: number; maxY: number; height: number },
  doors: { side: Facade; at: number[]; width: number; height: number },
) {
  const group = new THREE.Group()
  const { minX, maxX, minY, maxY, height } = box
  block(group, mat.wall, minX, maxX, minY, maxY, height)
  block(group, mat.roof, minX - 0.3, maxX + 0.3, minY - 0.3, maxY + 0.3, 0.5, height)
  block(group, mat.band, minX - 0.05, maxX + 0.05, minY - 0.05, maxY + 0.05, 1.1, height - 1.6)
  // Window ribbon on the long sides.
  const long = maxX - minX >= maxY - minY
  if (long) {
    block(group, mat.glass, minX + 2, maxX - 2, minY - 0.08, minY + 0.02, 1.4, height - 4.2)
    block(group, mat.glass, minX + 2, maxX - 2, maxY - 0.02, maxY + 0.08, 1.4, height - 4.2)
  } else {
    block(group, mat.glass, minX - 0.08, minX + 0.02, minY + 2, maxY - 2, 1.4, height - 4.2)
    block(group, mat.glass, maxX - 0.02, maxX + 0.08, minY + 2, maxY - 2, 1.4, height - 4.2)
  }
  // Rooftop units.
  for (let k = 0; k < 4; k++) {
    const cx = minX + ((k + 1) * (maxX - minX)) / 5
    const cy = (minY + maxY) / 2 + (k % 2 ? 4 : -4)
    block(group, mat.steel, cx - 1.6, cx + 1.6, cy - 1.2, cy + 1.2, 1.4, height + 0.5)
  }
  for (const c of doors.at) {
    const w = doors.width / 2
    const h = doors.height
    if (doors.side === 'e' || doors.side === 'w') {
      const x = doors.side === 'e' ? maxX : minX
      block(group, mat.door, x - 0.12, x + 0.12, c - w, c + w, h)
      block(group, mat.frame, x - 0.15, x + 0.15, c - w - 0.3, c - w, h + 0.3)
      block(group, mat.frame, x - 0.15, x + 0.15, c + w, c + w + 0.3, h + 0.3)
      block(group, mat.frame, x - 0.15, x + 0.15, c - w - 0.3, c + w + 0.3, 0.3, h)
    } else {
      const y = doors.side === 'n' ? minY : maxY
      block(group, mat.door, c - w, c + w, y - 0.12, y + 0.12, h)
      block(group, mat.frame, c - w - 0.3, c - w, y - 0.15, y + 0.15, h + 0.3)
      block(group, mat.frame, c + w, c + w + 0.3, y - 0.15, y + 0.15, h + 0.3)
      block(group, mat.frame, c - w - 0.3, c + w + 0.3, y - 0.15, y + 0.15, 0.3, h)
    }
  }
  parent.add(group)
  return group
}

function tree(parent: THREE.Object3D, mat: Palette, x: number, y: number, size = 1) {
  const trunk = new THREE.Mesh(
    new THREE.CylinderGeometry(0.18 * size, 0.25 * size, 2.4 * size, 8),
    mat.trunk,
  )
  trunk.position.set(x, 1.2 * size, y)
  parent.add(trunk)
  const crown = new THREE.Mesh(new THREE.SphereGeometry(2 * size, 12, 10), mat.leaves)
  crown.position.set(x, 3.6 * size, y)
  crown.scale.set(1, 1.15, 1)
  parent.add(crown)
}

function lightPole(parent: THREE.Object3D, mat: Palette, p: Pt, heading: number) {
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.12, 8, 8), mat.steel)
  pole.position.set(p.x, 4, p.y)
  parent.add(pole)
  // Arm reaching over the road (inward, towards the reference line).
  const arm = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.08, 2.2), mat.steel)
  arm.rotation.y = -heading
  arm.position.set(p.x + Math.sin(heading) * 1.1, 7.9, p.y - Math.cos(heading) * 1.1)
  parent.add(arm)
  const head = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.14, 0.32), mat.lamp)
  head.rotation.y = -heading
  head.position.set(p.x + Math.sin(heading) * 2.1, 7.8, p.y - Math.cos(heading) * 2.1)
  parent.add(head)
}

/** Car paints for parked scenery cars (staff car park, the yard's second row). */
const PAINTS = [0xe8e8e4, 0x1c1d20, 0x8f959c, 0x2c4a7c, 0x9b1b1b, 0xc4c7cb, 0x30343a, 0x5c6b7a]

function parkedCar(
  parent: THREE.Object3D,
  style: WorldStyle,
  x: number,
  y: number,
  yawDeg: number,
  k: number,
) {
  const car =
    style === 'tesla' ? createTeslaCar() : shadowed(createTeslaCar(PAINTS[k % PAINTS.length]))
  car.position.set(x, 0, y)
  car.rotation.y = (-yawDeg * Math.PI) / 180
  parent.add(car)
}

/**
 * The site around the roads.
 * @param style - Dashboard or camera look.
 * @returns Group named `site`.
 */
export function buildPlantSite(style: WorldStyle): THREE.Group {
  const mat = palette(style)
  const site = new THREE.Group()
  site.name = 'site'

  // Lawns inside the loop and along the south edge, under everything else.
  pad(site, mat.grass, 6, 98, -98, -6, 0.004)
  pad(site, mat.grass, 121, 138, -98, -6, 0.004)

  // Assembly hall (west): new cars come out of its east door onto the staging road.
  hall(
    site,
    mat,
    { minX: -120, maxX: STAGING.fromX, minY: -10, maxY: 28, height: 13 },
    {
      side: 'e',
      at: [LANE.outer],
      width: 5.5,
      height: 4.8,
    },
  )
  // Paint shop (south of the line corridor).
  hall(
    site,
    mat,
    { minX: 22, maxX: 134, minY: 28, maxY: 54, height: 14 },
    {
      side: 's',
      at: [40, 70, 100],
      width: 4.5,
      height: 4.5,
    },
  )
  // Rework hall (east): a door facing each bay on the shoulder.
  hall(
    site,
    mat,
    { minX: 155, maxX: 186, minY: -80, maxY: -22, height: 10 },
    {
      side: 'w',
      at: [-30, -40, -50, -60, -72],
      width: 4.2,
      height: 4.2,
    },
  )
  // Finishing hall (north).
  hall(
    site,
    mat,
    { minX: 22, maxX: 132, minY: -152, maxY: -119, height: 12 },
    {
      side: 's',
      at: [50, 80, 110],
      width: 5,
      height: 4.8,
    },
  )
  // Logistics warehouse (south-west), its dock facing the yard lot.
  hall(
    site,
    mat,
    { minX: -116, maxX: -58, minY: -104, maxY: -28, height: 12 },
    {
      side: 'e',
      at: [-40, -53, -66, -79, -92],
      width: 3.6,
      height: 4.2,
    },
  )
  // Office block inside the loop: glass on three floors.
  const office = new THREE.Group()
  block(office, mat.wall, 12, 48, -96, -70, 11)
  for (const floor of [1.2, 4.6, 8]) block(office, mat.glass, 11.9, 48.1, -96.1, -69.9, 2.2, floor)
  block(office, mat.roof, 11.6, 48.4, -96.4, -69.6, 0.4, 11)
  site.add(office)

  // The yard lot west of the slots (walkway, second parking row, truck aisle) and the staff
  // car park inside the loop, each with its stall lines and parked cars.
  pad(site, mat.lot, -58, -32.5, -100, -18, 0.008)
  pad(site, mat.lot, 12, 96, -44, -10, 0.008)
  let k = 0
  for (let y = -74; y <= -23.5; y += 6) {
    parkedCar(site, style, -38.25, y, 90, k++)
    block(site, mat.white, -41.2, -35.3, y + 2.95, y + 3.05, 0.02, 0.012)
  }
  for (const row of [-38, -16]) {
    for (let x = 16; x <= 92; x += 3) {
      block(site, mat.white, x - 0.05, x + 0.05, row - 2.6, row + 2.6, 0.02, 0.012)
      if ((x * 7 + row) % 11 < 6) parkedCar(site, style, x + 1.5, row, row < -20 ? 90 : -90, k++)
    }
  }

  // Inspection gate: two portal frames 9 m before and after the gate, joined by light bars
  // overhead. Open at the sides and roofless, so the four gate cameras (7 m ahead/behind, 5 m to
  // each side of the car) see the car unobstructed and in full daylight.
  const gate = { x: 32, y: LANE.edge / 2 }
  const tunnel = new THREE.Group()
  const half = 6.8
  for (const x of [gate.x - 9, gate.x + 9]) {
    for (const side of [-1, 1]) {
      block(
        tunnel,
        mat.canopy,
        x - 0.3,
        x + 0.3,
        gate.y + side * half - 0.3,
        gate.y + side * half + 0.3,
        5.6,
      )
    }
    block(tunnel, mat.canopy, x - 0.4, x + 0.4, gate.y - half - 0.4, gate.y + half + 0.4, 0.7, 5.6)
    block(tunnel, mat.band, x - 0.42, x + 0.42, gate.y - half, gate.y + half, 0.35, 5.75)
  }
  for (const side of [-1, 1]) {
    block(
      tunnel,
      mat.steel,
      gate.x - 9,
      gate.x + 9,
      gate.y + side * 3 - 0.12,
      gate.y + side * 3 + 0.12,
      0.2,
      6.1,
    )
    block(
      tunnel,
      mat.lamp,
      gate.x - 7,
      gate.x + 7,
      gate.y + side * 3 - 0.1,
      gate.y + side * 3 + 0.1,
      0.06,
      6.04,
    )
  }
  site.add(tunnel)

  // Test-track canopy (the R-12 lamp and brake checks run under it).
  const track = new THREE.Group()
  for (const x of [50, 62, 74]) {
    for (const side of [-0.6, LANE.shoulderEdge + 0.6])
      block(track, mat.steel, x - 0.15, x + 0.15, side - 0.15, side + 0.15, 5.6)
  }
  block(track, mat.canopy, 48, 76, -1.2, LANE.shoulderEdge + 1.2, 0.25, 5.6)
  site.add(track)

  // Trees on the lawns, light poles along the outside of the loop.
  for (const [x, y] of [
    [60, -60],
    [70, -78],
    [84, -64],
    [92, -88],
    [58, -90],
    [128, -20],
    [130, -50],
    [129, -80],
    [8, -52],
    [52, -52],
  ] as const) {
    tree(site, mat, x, y, 1 + ((x + y) % 3) * 0.12)
  }
  for (let s = 10; s < LOOP.total; s += 28) {
    const p = at(LOOP, s, LANE.shoulderEdge + 1.2)
    lightPole(site, mat, p, p.heading)
  }

  if (style === 'real') shadowed(site)
  return site
}
