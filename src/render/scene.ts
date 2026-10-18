import * as THREE from 'three'
import type { CarSnapshot } from '#/sim/vehicle'
import { type World, YARD_SLOTS } from '#/sim/world'
import { createCarMesh, updateCarMesh } from './cars'

export type ActorSnapshot = {
  id: string
  kind: 'box' | 'walker' | 'vehicle'
  x: number
  y: number
}

export type YardScene = {
  scene: THREE.Scene
  syncCars(snaps: CarSnapshot[]): void
  syncActors(actors: ActorSnapshot[]): void
  dispose(): void
}

const BACKGROUND = 0x0d1014
const ROAD_WIDTH = 5
const DASH_LENGTH = 3.2
const DASH_PERIOD = 7

function standard(colour: number, roughness = 0.9, metalness = 0): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({ color: colour, roughness, metalness })
}

function mesh(
  parent: THREE.Object3D,
  geometry: THREE.BufferGeometry,
  material: THREE.Material,
  x: number,
  y: number,
  z: number,
): THREE.Mesh {
  const item = new THREE.Mesh(geometry, material)
  item.position.set(x, y, z)
  item.castShadow = true
  item.receiveShadow = true
  parent.add(item)
  return item
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
): THREE.Mesh {
  return mesh(parent, new THREE.BoxGeometry(width, height, depth), material, x, y, z)
}

/** Flat marking / pad: receives shadows but never casts them. */
function flat(
  parent: THREE.Object3D,
  material: THREE.Material,
  width: number,
  depth: number,
  x: number,
  y: number,
  z: number,
): THREE.Mesh {
  const item = box(parent, material, width, 0.02, depth, x, y, z)
  item.castShadow = false
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
  return mesh(parent, new THREE.CylinderGeometry(radius, radius, height, 12), material, x, y, z)
}

function disposeObject(object: THREE.Object3D): void {
  object.traverse((node: THREE.Object3D) => {
    const item = node as THREE.Mesh
    item.geometry?.dispose()
    const material = item.material
    if (Array.isArray(material)) {
      for (const entry of material) entry.dispose()
    } else {
      material?.dispose()
    }
  })
}

export function createYardScene(world: World): YardScene {
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(BACKGROUND)
  scene.fog = new THREE.Fog(BACKGROUND, 170, 560)

  const points = Object.values(world.points)
  const minX = Math.min(...points.map((p) => p.x))
  const maxX = Math.max(...points.map((p) => p.x))
  const minY = Math.min(...points.map((p) => p.y))
  const maxY = Math.max(...points.map((p) => p.y))
  const centerX = (minX + maxX) / 2
  const centerZ = (minY + maxY) / 2

  const mat = {
    ground: standard(0x23272e, 1),
    apron: standard(0x2c313a, 1),
    road: standard(0x383d45, 1),
    edge: standard(0xbfc5cd, 0.7),
    dash: standard(0x9ea5ae, 0.7),
    slotSlab: standard(0x2c3138, 0.95),
    slotLine: standard(0x7d8590, 0.7),
    wall: standard(0x2e343c, 0.85),
    roof: standard(0x232830, 0.9),
    glass: standard(0x12161c, 0.25, 0.3),
    dark: standard(0x1a1e24, 0.85),
    steel: standard(0x8a929c, 0.45, 0.5),
    yellow: standard(0xd9a441, 0.6),
    crate: standard(0x6d5b45, 0.9),
    boxTruck: standard(0xd7d7d7, 0.6),
    cabTruck: standard(0x4f6b8f, 0.55, 0.2),
    tire: standard(0x141619, 0.95),
  }
  const lampWarm = new THREE.MeshStandardMaterial({
    color: 0xfff0b8,
    emissive: 0xffe9a8,
    emissiveIntensity: 1.4,
  })

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(900, 420), mat.ground)
  ground.rotation.x = -Math.PI / 2
  ground.position.set(centerX, 0, centerZ)
  ground.receiveShadow = true
  scene.add(ground)

  // Concrete aprons under each working zone, so the yard reads as plant floors.
  flat(scene, mat.apron, 200, 14, 91, 0.01, 40)
  flat(scene, mat.apron, 42, 44, 209, 0.01, 25)
  flat(scene, mat.apron, 54, 26, 163, 0.01, 13)
  flat(scene, mat.apron, 24, 16, 125, 0.01, 15)
  flat(scene, mat.apron, 58, 14, 153, 0.01, 72)
  flat(scene, mat.apron, 14, 14, 10, 0.01, 72)

  // Roads: asphalt with white edge lines and a dashed centre line.
  for (const segment of world.segments) {
    const from = world.point(segment.from)
    const to = world.point(segment.to)
    const dx = to.x - from.x
    const dz = to.y - from.y
    const length = Math.hypot(dx, dz)
    if (length === 0) continue
    const angle = -Math.atan2(dz, dx)
    const midX = (from.x + to.x) / 2
    const midZ = (from.y + to.y) / 2
    const px = (-dz / length) * (ROAD_WIDTH / 2 - 0.22)
    const pz = (dx / length) * (ROAD_WIDTH / 2 - 0.22)

    flat(scene, mat.road, length + ROAD_WIDTH, ROAD_WIDTH, midX, 0.02, midZ).rotation.y = angle
    for (const side of [1, -1]) {
      flat(scene, mat.edge, length, 0.14, midX + px * side, 0.04, midZ + pz * side).rotation.y =
        angle
    }

    const dashes = Math.max(1, Math.floor(length / DASH_PERIOD))
    for (let i = 0; i < dashes; i++) {
      const t = dashes === 1 ? 0.5 : (i * DASH_PERIOD + DASH_LENGTH / 2) / length
      const dashX = from.x + dx * t
      const dashZ = from.y + dz * t
      flat(scene, mat.dash, DASH_LENGTH, 0.14, dashX, 0.04, dashZ).rotation.y = angle
    }
  }

  // Line-end building: cars leave the assembly hall heading east.
  const line = world.point('line_end')
  box(scene, mat.wall, 16, 6, 12, line.x - 11, 3, line.y)
  box(scene, mat.roof, 17, 0.5, 13, line.x - 11, 6.25, line.y)
  box(scene, mat.glass, 0.24, 0.7, 12.2, line.x - 3, 4.5, line.y)
  box(scene, mat.dark, 0.3, 3.4, 5, line.x - 3, 1.7, line.y)

  // Inspection gantry with hazard stripes on the floor.
  const gate = world.point('inspection_gate')
  for (const side of [1, -1]) {
    box(scene, mat.steel, 0.3, 3.3, 0.3, gate.x, 1.65, gate.y + side * 3.4)
    flat(scene, mat.yellow, 0.35, 4.6, gate.x + side * 1.3, 0.04, gate.y)
  }
  box(scene, mat.steel, 0.36, 0.36, 7.1, gate.x, 3.4, gate.y)
  box(scene, mat.dark, 0.2, 0.7, 1.8, gate.x, 2.7, gate.y)
  box(scene, lampWarm, 0.5, 0.16, 0.4, gate.x, 3.16, gate.y - 2.2)
  box(scene, lampWarm, 0.5, 0.16, 0.4, gate.x, 3.16, gate.y + 2.2)

  // Test track light masts along the gate-to-track run.
  const track = world.point('test_track')
  for (let i = 1; i <= 3; i++) {
    const t = i / 4
    const x = gate.x + (track.x - gate.x) * t
    const z = gate.y + (track.y - gate.y) * t + (i % 2 === 0 ? 5 : -5)
    const toward = z > gate.y ? -1 : 1
    cylinder(scene, mat.steel, 0.24, 0.5, x, 0.25, z)
    cylinder(scene, mat.steel, 0.1, 4.4, x, 2.6, z)
    box(scene, mat.steel, 0.12, 0.12, 1.4, x, 4.7, z + toward * 0.7)
    box(scene, lampWarm, 0.55, 0.2, 0.4, x, 4.55, z + toward * 1.35)
  }

  // Report booth beside the report point.
  const report = world.point('report_point')
  box(scene, mat.wall, 3.2, 3, 3, report.x, 1.5, report.y - 9.5)
  box(scene, mat.roof, 3.8, 0.3, 3.6, report.x, 3.15, report.y - 9.5)
  box(scene, mat.glass, 2.6, 1, 0.15, report.x, 1.9, report.y - 7.95)

  // Rework hall with painted bays, doors facing the lane.
  const rework = [
    world.point('rework.mechanical'),
    world.point('rework.electrical'),
    world.point('rework.body'),
    world.point('rework.paint'),
  ]
  box(scene, mat.wall, 58, 5.5, 8, 153, 2.75, 79.5)
  box(scene, mat.roof, 59, 0.45, 9, 153, 5.7, 79.5)
  box(scene, mat.glass, 59, 0.6, 0.2, 153, 4.5, 75.4)
  for (const bay of rework) {
    box(scene, mat.dark, 6, 3.6, 0.3, bay.x, 1.8, 75.45)
    flat(scene, mat.apron, 7, 5.4, bay.x, 0.05, bay.y)
    flat(scene, mat.slotLine, 7.3, 0.12, bay.x, 0.07, bay.y - 2.75)
    flat(scene, mat.slotLine, 7.3, 0.12, bay.x, 0.07, bay.y + 2.75)
    flat(scene, mat.slotLine, 0.12, 5.4, bay.x - 3.65, 0.07, bay.y)
    flat(scene, mat.slotLine, 0.12, 5.4, bay.x + 3.65, 0.07, bay.y)
  }

  // Buffer zone: pallet of crates.
  const buffer = world.point('buffer_zone_b')
  for (const x of [-1, 0]) {
    box(scene, mat.crate, 1.2, 1.2, 1.2, buffer.x + x * 1.3, 0.62, buffer.y - 2.5)
    box(scene, mat.crate, 1.2, 1.2, 1.2, buffer.x + x * 1.3, 1.84, buffer.y - 2.5)
  }

  // Yard slots with painted outlines.
  for (const slot of YARD_SLOTS) {
    const point = world.point(slot)
    flat(scene, mat.slotSlab, 6, 4, point.x, 0.06, point.y)
    flat(scene, mat.slotLine, 6.2, 0.12, point.x, 0.08, point.y - 2.06)
    flat(scene, mat.slotLine, 6.2, 0.12, point.x, 0.08, point.y + 2.06)
    flat(scene, mat.slotLine, 0.12, 4.2, point.x - 3.16, 0.08, point.y)
    flat(scene, mat.slotLine, 0.12, 4.2, point.x + 3.16, 0.08, point.y)
  }

  // Loading dock: canopy on posts plus a box truck at the dock edge.
  const loading = world.point('loading_bay')
  for (const x of [119, 133]) {
    for (const z of [11, 19]) cylinder(scene, mat.steel, 0.13, 5, x, 2.5, z)
  }
  box(scene, mat.roof, 16, 0.4, 10, 126, 5, 15)
  const truckZ = loading.y + 7.5
  box(scene, mat.boxTruck, 8, 2.6, 2.5, 120, 1.85, truckZ)
  box(scene, mat.cabTruck, 2.2, 1.9, 2.3, 125.2, 1.3, truckZ)
  box(scene, mat.glass, 0.12, 0.9, 2.1, 126.35, 1.9, truckZ)
  box(scene, mat.yellow, 0.25, 0.35, 2.3, 126.45, 0.55, truckZ)
  for (const x of [116.3, 118.3, 124.6]) {
    for (const z of [truckZ - 1.05, truckZ + 1.05]) {
      const wheel = cylinder(scene, mat.tire, 0.5, 0.3, x, 0.5, z)
      wheel.rotation.x = Math.PI / 2
    }
  }

  // Yard light poles along the main roads.
  for (const [x, z] of [
    [28, 31],
    [28, 59],
    [98, 31],
    [98, 59],
    [196, 31],
    [168, 28],
    [140, 28],
  ]) {
    cylinder(scene, mat.steel, 0.1, 5.2, x, 2.6, z)
    box(scene, lampWarm, 0.55, 0.2, 0.4, x, 5.1, z)
  }

  // Key light with shadows plus a cool fill and ambient base.
  const sun = new THREE.DirectionalLight(0xffffff, 2.6)
  sun.position.set(centerX + 58, 82, centerZ + 58)
  sun.target.position.set(centerX, 0, centerZ)
  sun.castShadow = true
  sun.shadow.mapSize.width = 1024
  sun.shadow.mapSize.height = 1024
  sun.shadow.camera.left = -150
  sun.shadow.camera.right = 150
  sun.shadow.camera.top = 150
  sun.shadow.camera.bottom = -150
  sun.shadow.camera.near = 10
  sun.shadow.camera.far = 400
  sun.shadow.camera.updateProjectionMatrix()
  sun.shadow.bias = -0.0004
  sun.shadow.normalBias = 0.02
  scene.add(sun)
  scene.add(sun.target)
  const fill = new THREE.DirectionalLight(0xbfd4ff, 0.35)
  fill.position.set(centerX - 60, 40, centerZ - 80)
  scene.add(fill)
  scene.add(new THREE.AmbientLight(0xffffff, 0.7))

  const cars = new Map<string, THREE.Group>()
  const actors = new Map<string, THREE.Object3D>()

  function syncCars(snaps: CarSnapshot[]): void {
    const seen = new Set<string>()
    for (const car of snaps) {
      seen.add(car.vin)
      const existing = cars.get(car.vin)
      if (existing) {
        updateCarMesh(existing, car)
        continue
      }
      const item = createCarMesh(car)
      cars.set(car.vin, item)
      scene.add(item)
    }
    for (const [vin, item] of cars) {
      if (seen.has(vin)) continue
      scene.remove(item)
      disposeObject(item)
      cars.delete(vin)
    }
  }

  function createActor(actor: ActorSnapshot): THREE.Object3D {
    if (actor.kind === 'walker') {
      const group = new THREE.Group()
      const torso = new THREE.Mesh(
        new THREE.CapsuleGeometry(0.35, 0.9, 4, 8),
        standard(0xf08a24, 0.8),
      )
      torso.position.y = 0.8
      torso.castShadow = true
      const helmet = new THREE.Mesh(new THREE.SphereGeometry(0.19, 10, 8), standard(0xf2c94c, 0.5))
      helmet.position.y = 1.52
      helmet.castShadow = true
      group.add(torso, helmet)
      return group
    }
    if (actor.kind === 'vehicle') {
      const item = new THREE.Mesh(new THREE.BoxGeometry(4.2, 1.4, 1.8), standard(0x4f6b8f, 0.6))
      item.position.y = 0.7
      item.castShadow = true
      return item
    }
    const item = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.9, 0.9), standard(0xb07b4f, 0.9))
    item.position.y = 0.45
    item.castShadow = true
    return item
  }

  function syncActors(snaps: ActorSnapshot[]): void {
    const seen = new Set<string>()
    for (const actor of snaps) {
      seen.add(actor.id)
      let object = actors.get(actor.id)
      if (!object) {
        object = createActor(actor)
        actors.set(actor.id, object)
        scene.add(object)
      }
      object.position.x = actor.x
      object.position.z = actor.y
    }
    for (const [id, object] of actors) {
      if (seen.has(id)) continue
      scene.remove(object)
      disposeObject(object)
      actors.delete(id)
    }
  }

  function dispose(): void {
    const geometries = new Set<THREE.BufferGeometry>()
    const materials = new Set<THREE.Material>()
    scene.traverse((node: THREE.Object3D) => {
      const item = node as THREE.Mesh
      if (item.geometry) geometries.add(item.geometry)
      const material = item.material
      if (Array.isArray(material)) {
        for (const entry of material) materials.add(entry)
      } else if (material) {
        materials.add(material)
      }
    })
    for (const geometry of geometries) geometry.dispose()
    for (const material of materials) material.dispose()
    cars.clear()
    actors.clear()
    scene.clear()
  }

  return { scene, syncCars, syncActors, dispose }
}
