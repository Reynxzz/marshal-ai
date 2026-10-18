// three.js scene for the dashboard 3D view: the TEAM_PLAN plant (road strips between the named
// points, gate, test track, rework bay, yard slots, dock) plus cars, props and focus highlights.
// Builds without a WebGL context, so it is testable in jsdom.

import * as THREE from 'three'
import {
  type CarFault,
  type CarLamps,
  createCarMesh as createEngineCar,
  updateCarMesh as placeEngineCar,
  setCarFaults,
  setCarLamps,
} from '#/lib/simulator/actors/car'
import {
  buildCompound,
  buildFence,
  buildGateProps,
  buildReworkProps,
  buildYardCars,
} from '#/lib/simulator/world/dress'
import { addDuskLighting } from '#/lib/simulator/world/lighting'
import { boundsOf, createLaneGraph } from '#/lib/simulator/world/plantMap'
import { buildRoads } from '#/lib/simulator/world/roadBuilder'
import { createCarMesh, updateCarMesh } from '#/render/cars'
import type { CarSnapshot } from '#/sim/vehicle'
import { focusColour } from './focus'
import { DOCK_POSE, type PlantLayout, type PlantMarker, toThree } from './plantMap'
import type { SimCar, SimProp, SimPropKind, SimSnapshot } from './snapshot'

/** A world-space anchor for an HTML label. */
export type LabelAnchor = {
  id: string
  text: string
  /** 'marker' = plant point, 'focus' = the focused car. */
  kind: 'marker' | 'focus'
  position: [number, number, number]
  /** Highlight colour for focus labels. */
  colour?: string
  /** Only shown when the camera is within {@link NEAR_LABEL_M} (yard slots). */
  near?: boolean
}

/** Camera distance under which `near` labels (Y1..Y8) appear. */
export const NEAR_LABEL_M = 70

/** Lamp state of one car as the engine drives it (dressed scene). */
export type CarLook = { lamps: CarLamps; faults: CarFault[] }

/** Options for {@link createPlantScene}. */
export type PlantSceneOptions = {
  /**
   * The engine's dressed world: lane-graph roads, fence, yard scenery, gate and rework props,
   * dusk light, and the engine car model whose lamps follow `carLook` (what the cameras see).
   */
  dressed?: boolean
  /** Lamp state per car for the dressed car model; lamps stay off when it returns null. */
  carLook?: (vin: string) => CarLook | null
}

export type PlantScene = {
  scene: THREE.Scene
  /** Shows or hides the focus beacon and highlight rings (hidden for camera frames). */
  setOverlays(visible: boolean): void
  /** Syncs cars, props, trucks and highlights; `time` (seconds) animates the beacon. */
  sync(snapshot: SimSnapshot, time?: number): void
  /** Label anchors for the current frame (markers first, then the focused car). */
  labels(): LabelAnchor[]
  /** VINs with a car mesh (for tests). */
  carIds(): string[]
  /** Prop ids with a mesh (for tests). */
  propIds(): string[]
  dispose(): void
}

const BACKGROUND = 0x12171d
const DUSK_SKY = 0x2a3442
const ROAD_WIDTH = 5
const AMBER = '#E39B2E'
const BLUE = '#7FA7FF'

function standard(colour: number | string, roughness = 0.9, metalness = 0) {
  return new THREE.MeshStandardMaterial({ color: colour, roughness, metalness })
}

function add(
  parent: THREE.Object3D,
  geometry: THREE.BufferGeometry,
  material: THREE.Material,
  x: number,
  y: number,
  z: number,
): THREE.Mesh {
  const item = new THREE.Mesh(geometry, material)
  item.position.set(x, y, z)
  parent.add(item)
  return item
}

function ring(colour: string, inner: number, outer: number): THREE.Mesh {
  const material = new THREE.MeshBasicMaterial({
    color: colour,
    transparent: true,
    opacity: 0.85,
    side: THREE.DoubleSide,
  })
  const item = new THREE.Mesh(new THREE.RingGeometry(inner, outer, 40), material)
  item.rotation.x = -Math.PI / 2
  item.position.y = 0.05
  return item
}

type Materials = ReturnType<typeof materials>

function materials() {
  return {
    ground: standard(0x1b222b, 1),
    road: standard(0x2f3742, 0.95),
    edge: standard(0x56606e, 0.8),
    pad: standard(0x263040, 0.95),
    gate: standard(0xd9d5cb, 0.5, 0.3),
    track: standard(0x2a3a52, 0.9),
    rework: standard(0xc2621f, 0.8),
    slot: standard(0x344052, 0.9),
    slotLine: standard(0xa9b1bc, 0.7),
    dock: standard(0x3a3326, 0.9),
    box: standard(0x8a6a44, 0.9),
    hiVis: standard(0xf28c28, 0.6),
    skin: standard(0xd8b48a, 0.8),
    cone: standard(0xff7a1a, 0.6),
    barrel: standard(0x2f6fed, 0.6),
    truckBox: standard(0xd7d7d7, 0.6),
    truckCab: standard(0x4f6b8f, 0.55, 0.2),
  }
}

function buildMarker(parent: THREE.Object3D, marker: PlantMarker, mat: Materials): void {
  const { position, rotationY } = toThree(marker.x, marker.y, marker.yaw)
  const group = new THREE.Group()
  group.position.set(position[0], 0, position[2])
  group.rotation.y = rotationY
  parent.add(group)
  switch (marker.kind) {
    case 'gate': {
      // Arch across the lane: two posts and a beam.
      for (const side of [-1, 1])
        add(group, new THREE.BoxGeometry(0.4, 4.2, 0.4), mat.gate, 0, 2.1, side * 3.2)
      add(group, new THREE.BoxGeometry(0.6, 0.5, 6.8), mat.gate, 0, 4.2, 0)
      break
    }
    case 'track':
      add(group, new THREE.BoxGeometry(30, 0.04, 8), mat.track, 0, 0.03, 0)
      break
    case 'rework':
      add(group, new THREE.BoxGeometry(8, 0.04, 6), mat.rework, 0, 0.03, 0)
      add(group, new THREE.BoxGeometry(0.3, 3, 6), mat.rework, -4, 1.5, 0)
      break
    case 'slot':
      add(group, new THREE.BoxGeometry(5.2, 0.03, 2.6), mat.slot, 0, 0.03, 0)
      for (const side of [-1, 1])
        add(group, new THREE.BoxGeometry(5.2, 0.04, 0.12), mat.slotLine, 0, 0.05, side * 1.3)
      break
    case 'dock':
      add(group, new THREE.BoxGeometry(22, 0.05, 12), mat.dock, 0, 0.03, 0)
      break
    default:
      add(group, new THREE.CylinderGeometry(1.2, 1.2, 0.05, 20), mat.pad, 0, 0.04, 0)
      add(group, new THREE.CylinderGeometry(0.12, 0.12, 2.4, 8), mat.edge, 0, 1.2, 0)
  }
}

function buildProp(kind: SimPropKind, mat: Materials): THREE.Group {
  const group = new THREE.Group()
  switch (kind) {
    case 'box':
      add(group, new THREE.BoxGeometry(1.2, 1, 1.2), mat.box, 0, 0.5, 0)
      break
    case 'worker':
      add(group, new THREE.CylinderGeometry(0.28, 0.28, 1.3, 10), mat.hiVis, 0, 0.65, 0)
      add(group, new THREE.SphereGeometry(0.2, 12, 10), mat.skin, 0, 1.5, 0)
      break
    case 'cone':
      add(group, new THREE.ConeGeometry(0.22, 0.7, 12), mat.cone, 0, 0.35, 0)
      break
    case 'barrel':
      add(group, new THREE.CylinderGeometry(0.3, 0.3, 0.9, 12), mat.barrel, 0, 0.45, 0)
      break
    case 'truck':
      buildTruck(group, mat)
      break
  }
  const halo = ring(AMBER, kind === 'truck' ? 5 : 1, kind === 'truck' ? 5.5 : 1.3)
  halo.name = 'halo'
  halo.visible = false
  group.add(halo)
  return group
}

function buildTruck(group: THREE.Object3D, mat: Materials): void {
  add(group, new THREE.BoxGeometry(6, 2.8, 2.5), mat.truckBox, -1, 1.8, 0)
  add(group, new THREE.BoxGeometry(2, 2.2, 2.4), mat.truckCab, 3, 1.4, 0)
}

const OFF_LAMPS: CarLamps = {
  headlights: false,
  brake_lights: false,
  reverse: false,
  indicator_left: false,
  indicator_right: false,
}

function carSnapshot(car: SimCar): CarSnapshot {
  // src/render/cars expects yaw in radians; its pose mapping matches toThree().
  return {
    vin: car.vin,
    x: car.x,
    y: car.y,
    yaw: (car.yaw * Math.PI) / 180,
    speedKmh: 0,
    state: 'moving',
    colour: car.colour,
    lamps: { headlights: true, brake_lights: false },
  }
}

/**
 * Builds the plant scene.
 * @param layout - Plant layout from `buildPlantLayout`.
 * @returns Scene with sync, labels and dispose.
 */
export function createPlantScene(layout: PlantLayout, options: PlantSceneOptions = {}): PlantScene {
  const dressed = options.dressed === true
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(dressed ? DUSK_SKY : BACKGROUND)
  scene.fog = new THREE.Fog(dressed ? DUSK_SKY : BACKGROUND, 260, 700)
  const mat = materials()

  if (dressed) {
    scene.add(new THREE.HemisphereLight(0xc9d4e6, 0x1b222b, 0.45))
    addDuskLighting(scene, layout.center)
  } else {
    scene.add(new THREE.HemisphereLight(0xdfe6f0, 0x1b222b, 0.9))
    const sun = new THREE.DirectionalLight(0xffffff, 1.6)
    sun.position.set(layout.center.x + 60, 120, layout.center.y + 40)
    scene.add(sun)
  }

  const { bounds, center } = layout
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(bounds.maxX - bounds.minX + 160, bounds.maxY - bounds.minY + 160),
    mat.ground,
  )
  ground.rotation.x = -Math.PI / 2
  ground.position.set(center.x, 0, center.y)
  scene.add(ground)

  const map = new THREE.Group()
  scene.add(map)
  const graph = dressed ? createLaneGraph() : null
  if (graph) {
    map.add(buildRoads(graph))
    map.add(buildFence(boundsOf(graph.points)))
    map.add(buildYardCars(graph.points))
    map.add(buildCompound(graph.points))
    map.add(buildGateProps(graph.points))
    map.add(buildReworkProps(graph.points))
  }
  for (const road of graph ? [] : layout.roads) {
    const { rotationY } = toThree(road.cx, road.cy, road.yaw)
    const strip = add(
      map,
      new THREE.BoxGeometry(road.length + ROAD_WIDTH, 0.02, ROAD_WIDTH),
      mat.road,
      road.cx,
      0.01,
      road.cy,
    )
    strip.rotation.y = rotationY
  }
  // Staging lane behind the line end: spawned cars queue here before their first mission.
  const lineEnd = layout.markers.find((marker) => marker.name === 'line_end')
  if (lineEnd) {
    const staging = 100
    add(
      map,
      new THREE.BoxGeometry(staging, 0.02, ROAD_WIDTH),
      mat.road,
      lineEnd.x - staging / 2,
      0.01,
      lineEnd.y,
    )
  }
  for (const marker of layout.markers) buildMarker(map, marker, mat)

  const shared = new Set<THREE.Material>(Object.values(mat))
  /** Removes an object and frees its own geometries and materials (shared ones stay). */
  const release = (object: THREE.Object3D): void => {
    scene.remove(object)
    object.traverse((node: THREE.Object3D) => {
      const item = node as THREE.Mesh
      item.geometry?.dispose()
      const list = Array.isArray(item.material)
        ? item.material
        : item.material
          ? [item.material]
          : []
      for (const material of list) if (!shared.has(material)) material.dispose()
    })
  }

  const cars = new Map<string, { group: THREE.Group; halo: THREE.Mesh }>()
  const props = new Map<string, { group: THREE.Group; kind: SimPropKind }>()
  const trucks = new Map<string, THREE.Group>()
  const beacon = add(
    scene,
    new THREE.ConeGeometry(0.7, 1.6, 16),
    new THREE.MeshBasicMaterial({ color: AMBER }),
    0,
    5,
    0,
  )
  beacon.rotation.x = Math.PI
  beacon.visible = false
  let focusLabel: LabelAnchor | null = null
  let overlays = true

  const syncCars = (list: SimCar[]): void => {
    const seen = new Set<string>()
    for (const car of list) {
      seen.add(car.vin)
      let entry = cars.get(car.vin)
      if (!entry) {
        const group = dressed ? createEngineCar(car.colour) : createCarMesh(carSnapshot(car))
        const halo = ring(AMBER, 2.8, 3.4)
        group.add(halo)
        scene.add(group)
        entry = { group, halo }
        cars.set(car.vin, entry)
      }
      if (dressed) {
        placeEngineCar(entry.group, car)
        const look = options.carLook?.(car.vin)
        setCarLamps(entry.group, look?.lamps ?? OFF_LAMPS)
        setCarFaults(entry.group, new Set(look?.faults ?? []))
      } else {
        updateCarMesh(entry.group, carSnapshot(car))
      }
      entry.halo.visible = overlays && car.highlight !== null
      if (car.highlight) entry.halo.material.color.set(car.highlight === 'selected' ? BLUE : AMBER)
    }
    for (const [vin, entry] of cars) {
      if (seen.has(vin)) continue
      release(entry.group)
      cars.delete(vin)
    }
  }

  const syncProps = (list: SimProp[]): void => {
    const seen = new Set<string>()
    for (const prop of list) {
      seen.add(prop.id)
      let entry = props.get(prop.id)
      if (!entry || entry.kind !== prop.kind) {
        if (entry) release(entry.group)
        entry = { group: buildProp(prop.kind, mat), kind: prop.kind }
        scene.add(entry.group)
        props.set(prop.id, entry)
      }
      const { position, rotationY } = toThree(prop.x, prop.y, prop.yaw)
      entry.group.position.set(...position)
      entry.group.rotation.y = rotationY
      const halo = entry.group.getObjectByName('halo')
      if (halo) halo.visible = overlays && prop.blocking
    }
    for (const [id, entry] of props) {
      if (seen.has(id)) continue
      release(entry.group)
      props.delete(id)
    }
  }

  const syncTrucks = (ids: string[]): void => {
    const seen = new Set(ids)
    ids.forEach((id, index) => {
      let group = trucks.get(id)
      if (!group) {
        group = new THREE.Group()
        buildTruck(group, mat)
        scene.add(group)
        trucks.set(id, group)
      }
      const { position, rotationY } = toThree(DOCK_POSE.x, DOCK_POSE.y + (index - 1) * 4, 180)
      group.position.set(...position)
      group.rotation.y = rotationY
    })
    for (const [id, group] of trucks) {
      if (seen.has(id)) continue
      release(group)
      trucks.delete(id)
    }
  }

  const sync = (snapshot: SimSnapshot, time = 0): void => {
    syncCars(snapshot.cars)
    syncProps(snapshot.props)
    syncTrucks(snapshot.trucks.slice(0, 3).map((truck) => truck.id))
    const { focus, focusPose } = snapshot
    if (focus && focusPose) {
      const colour = focusColour(focus)
      beacon.visible = overlays
      beacon.material.color.set(colour)
      beacon.position.set(focusPose.x, 4.4 + Math.sin(time * 3) * 0.3, focusPose.y)
      focusLabel = {
        id: `focus-${focus.vin}`,
        text: `${focus.vin} · ${focus.location}`,
        kind: 'focus',
        position: [focusPose.x, 6.2, focusPose.y],
        colour,
      }
    } else {
      beacon.visible = false
      focusLabel = null
    }
  }

  const markerLabels: LabelAnchor[] = layout.markers.map((marker) => ({
    id: `marker-${marker.name}`,
    text: marker.label,
    kind: 'marker',
    position: [marker.x, marker.kind === 'gate' ? 5.2 : 2.8, marker.y],
    ...(marker.kind === 'slot' || marker.kind === 'buffer' ? { near: true } : {}),
  }))

  const dispose = (): void => {
    const geometries = new Set<THREE.BufferGeometry>()
    const used = new Set<THREE.Material>()
    scene.traverse((node: THREE.Object3D) => {
      const item = node as THREE.Mesh
      if (item.geometry) geometries.add(item.geometry)
      const material = item.material
      if (Array.isArray(material)) for (const entry of material) used.add(entry)
      else if (material) used.add(material)
    })
    for (const material of shared) used.add(material)
    for (const geometry of geometries) geometry.dispose()
    for (const material of used) material.dispose()
    cars.clear()
    props.clear()
    trucks.clear()
    scene.clear()
  }

  const setOverlays = (visible: boolean): void => {
    overlays = visible
    if (!visible) {
      beacon.visible = false
      for (const entry of cars.values()) entry.halo.visible = false
      for (const entry of props.values()) {
        const halo = entry.group.getObjectByName('halo')
        if (halo) halo.visible = false
      }
    }
  }

  return {
    scene,
    setOverlays,
    sync,
    labels: () => (focusLabel ? [...markerLabels, focusLabel] : markerLabels),
    carIds: () => [...cars.keys()],
    propIds: () => [...props.keys()],
    dispose,
  }
}
