// Live backend for the dashboard: the ThreeJsEngine drives the cars (pure pursuit, queuing,
// R-12, assist, kinematic parking) and the cameras render the engine's dressed plant scene, so
// the gate, lamp and assist checks send real JPEGs to Gemini instead of placeholder frames.
// Frames are rendered on demand (gate arrival, each R-12 lamp step, an assist request) from a
// scene synced to the engine at that moment; overlays stay hidden, so frames are clean (M3.1).

import * as THREE from 'three'
import { POINTS } from '#/lib/marshal/points'
import { rgbToColourName } from '#/lib/marshal/state'
import type { GateFrames, GateView } from '#/lib/marshal/types'
import { buildPlantLayout } from '#/lib/simview/plantMap'
import { createPlantScene, type PlantScene } from '#/lib/simview/scene'
import { colourHexFor, type SimSnapshot } from '#/lib/simview/snapshot'
import type { CarFault } from './actors/car'
import { CAR_FRAME_HEIGHT, CAR_FRAME_WIDTH, carCameraPoses } from './cameras/carCamera'
import { GATE_FRAME_HEIGHT, GATE_FRAME_WIDTH, gateCameraPoses } from './cameras/gateCamera'
import { carlaToThree, carlaYawToThree } from './coords'
import { ThreeJsEngine } from './engine'

/** JPEG quality for camera frames sent to Gemini. */
export const LIVE_JPEG_QUALITY = 0.85

/**
 * Field of view of the front/rear gate views. TEAM_PLAN's 90 degrees leaves the car at about
 * 13% of the frame width from 7 m; the side views (5 m, 4.9 m long car) keep 90.
 */
export const GATE_END_FOV = 55

/** Assist clip length: frames rendered from the chase camera when a car asks for help. */
export const LIVE_ASSIST_FRAMES = 2

/** Other cars this close to a camera are left out of its frame (a camera cannot sit inside one). */
export const CAMERA_CLEARANCE_M = 3.5

/** Renders one camera view of the scene to a base64 JPEG. */
type Shoot = (pose: CameraPose, width: number, height: number) => string

type CameraPose = { x: number; y: number; z: number; yaw: number; pitch: number; fov: number }

/** The live engine as the shift store sees it. */
export type LiveBackend = ThreeJsEngine & {
  readonly placeholderFrames: false
  readonly laneApproach: true
  capture(vin: string, views: GateView[]): Promise<GateFrames>
  getEvidenceFrames(vin: string, camera: 'front' | 'rear'): string[]
  /** Frees the camera renderer and scene. */
  dispose(): void
}

/**
 * Creates the camera renderer (browser only). Returns null without WebGL; the backend then
 * returns no frames and the flows fall back to their offline answers.
 */
function createShooter(scene: THREE.Scene): { shoot: Shoot; dispose(): void } | null {
  if (typeof document === 'undefined') return null
  let renderer: THREE.WebGLRenderer
  try {
    const canvas = document.createElement('canvas')
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true })
  } catch {
    return null
  }
  renderer.setPixelRatio(1)
  renderer.outputColorSpace = THREE.SRGBColorSpace
  // Daylight with soft sun shadows and a filmic response (realLook.ts), closer to a real camera.
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.05
  const camera = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 600)
  return {
    shoot(pose, width, height) {
      renderer.setSize(width, height, false)
      camera.fov = pose.fov
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      const [x, y, z] = carlaToThree(pose.x, pose.y, pose.z)
      camera.position.set(x, y, z)
      camera.rotation.order = 'YXZ'
      // The rig yaw convention: 0 looks along +x. A three.js camera looks down -z, so turn it
      // a quarter right first.
      camera.rotation.y = carlaYawToThree(pose.yaw) - Math.PI / 2
      camera.rotation.x = (pose.pitch * Math.PI) / 180
      renderer.render(scene, camera)
      const url = renderer.domElement.toDataURL('image/jpeg', LIVE_JPEG_QUALITY)
      return url.slice(url.indexOf(',') + 1)
    },
    dispose() {
      renderer.dispose()
    },
  }
}

/**
 * Builds the live backend: a ThreeJsEngine whose camera ports render real frames.
 * @returns Engine plus the gate and evidence ports.
 */
export function createLiveBackend(): LiveBackend {
  let plant: PlantScene | null = null
  let shooter: ReturnType<typeof createShooter> | undefined
  const layout = buildPlantLayout()

  const engine = new ThreeJsEngine(undefined, {
    assistFrames: (vin) => assistClip(vin),
    animateParking: true,
  })

  /**
   * Syncs the camera scene to the engine and returns the renderer (null without WebGL).
   * @param cameras - Camera positions; other cars within CAMERA_CLEARANCE_M of one are hidden.
   * @param subject - The car being filmed (always kept).
   */
  const ready = (cameras: readonly { x: number; y: number }[] = [], subject = ''): Shoot | null => {
    if (!plant) {
      plant = createPlantScene(layout, {
        dressed: true,
        carLook: (vin) => ({
          lamps: engine.lampsOf(vin),
          faults: engine.faultsOf(vin) as CarFault[],
        }),
      })
      plant.setOverlays(false)
    }
    if (shooter === undefined) shooter = createShooter(plant.scene)
    if (!shooter) return null
    const snapshot: SimSnapshot = {
      cars: engine
        .listCars()
        .filter(
          (car) =>
            car.vin === subject ||
            cameras.every((cam) => Math.hypot(cam.x - car.x, cam.y - car.y) > CAMERA_CLEARANCE_M),
        )
        .map((car) => ({
          vin: car.vin,
          x: car.x,
          y: car.y,
          yaw: car.yaw,
          colour: colourHexFor(rgbToColourName(car.rgb)),
          highlight: null,
        })),
      props: engine.listProps().map((prop) => ({ ...prop, blocking: false })),
      trucks: [],
      focus: null,
      focusPose: null,
    }
    plant.sync(snapshot)
    const centre = engine.listCars().find((car) => car.vin === subject) ?? cameras[0]
    if (centre) plant.focusLight(centre.x, centre.y)
    return shooter.shoot
  }

  const carView = (vin: string, view: 'assist' | 'evidence_front' | 'evidence_rear') => {
    const pose = engine.getPositionXY(vin)
    return carCameraPoses(pose).find((item) => item.view === view)
  }

  function assistClip(vin: string): string[] {
    const pose = carView(vin, 'assist')
    const shoot = pose ? ready([pose], vin) : null
    if (!shoot || !pose) return []
    const frame = shoot(pose, CAR_FRAME_WIDTH, CAR_FRAME_HEIGHT)
    return Array.from({ length: LIVE_ASSIST_FRAMES }, () => frame)
  }

  return Object.assign(engine, {
    placeholderFrames: false as const,
    laneApproach: true as const,
    async capture(vin: string, views: GateView[]): Promise<GateFrames> {
      const frames: GateFrames = { front: '', rear: '', left: '', right: '' }
      // The rig is centred on where the car actually stopped (the arrival radius leaves it a few
      // metres short of the gate point), facing along the gate lane; the end views zoom in.
      const car = engine.getPositionXY(vin)
      const gate = POINTS.inspection_gate
      const poses = gateCameraPoses({ x: car.x, y: car.y, yaw: gate.yaw }).map((pose) =>
        pose.view === 'front' || pose.view === 'rear' ? { ...pose, fov: GATE_END_FOV } : pose,
      )
      const shoot = ready(poses, vin)
      if (!shoot) return frames
      for (const pose of poses) {
        if (views.includes(pose.view)) {
          frames[pose.view] = shoot(pose, GATE_FRAME_WIDTH, GATE_FRAME_HEIGHT)
        }
      }
      return frames
    },
    getEvidenceFrames(vin: string, camera: 'front' | 'rear'): string[] {
      const pose = carView(vin, camera === 'front' ? 'evidence_front' : 'evidence_rear')
      const shoot = pose ? ready([pose], vin) : null
      if (!shoot || !pose) return []
      return [shoot(pose, CAR_FRAME_WIDTH, CAR_FRAME_HEIGHT)]
    },
    dispose() {
      shooter?.dispose()
      plant?.dispose()
      shooter = undefined
      plant = null
    },
  })
}
