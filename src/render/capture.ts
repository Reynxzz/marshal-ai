import * as THREE from 'three'
import type { AiFrame } from '#/ai/tasks'
import { GATE_VIEWS, truthCapture, truthForCar } from '#/ai/truth'
import type { CapturePort } from '#/sim/ports'
import type { CarSnapshot } from '#/sim/vehicle'
import type { World } from '#/sim/world'
import { createYardScene, type YardScene } from './scene'

export type CaptureMode = 'render' | 'truth'

const WIDTH = 640
const HEIGHT = 400
const GATE_CAMERA_HEIGHT = 1.5
const GATE_CAMERA_DISTANCE = 6
const TRACK_CAMERA_HEIGHT = 2

type Rig = {
  yard: YardScene
  renderer: THREE.WebGLRenderer
  canvas: HTMLCanvasElement
  camera: THREE.PerspectiveCamera
}

function offset(car: CarSnapshot, distance: number, side: number): THREE.Vector3 {
  const cos = Math.cos(car.yaw)
  const sin = Math.sin(car.yaw)
  return new THREE.Vector3(cos * distance - sin * side, 0, sin * distance + cos * side)
}

function frame(rig: Rig, car: CarSnapshot, camera: THREE.PerspectiveCamera, view: string): AiFrame {
  rig.yard.syncCars([car])
  rig.renderer.render(rig.yard.scene, camera)
  return {
    view,
    mime: 'image/jpeg',
    dataUrl: rig.canvas.toDataURL('image/jpeg', 0.85),
    width: WIDTH,
    height: HEIGHT,
    truth: truthForCar(car),
  }
}

export function createCapture(world: World, opts: { mode?: CaptureMode } = {}): CapturePort {
  const mode = opts.mode ?? 'render'
  let rig: Rig | null = null
  let triedWebGL = false

  function getRig(): Rig | null {
    if (mode === 'truth') return null
    if (triedWebGL) return rig
    triedWebGL = true

    try {
      const canvas = document.createElement('canvas')
      canvas.width = WIDTH
      canvas.height = HEIGHT
      canvas.style.display = 'none'
      const renderer = new THREE.WebGLRenderer({
        canvas,
        antialias: false,
        preserveDrawingBuffer: true,
      })
      renderer.setSize(WIDTH, HEIGHT, false)
      renderer.setPixelRatio(1)
      renderer.outputColorSpace = THREE.SRGBColorSpace
      renderer.shadowMap.enabled = true
      const camera = new THREE.PerspectiveCamera(50, WIDTH / HEIGHT, 0.1, 500)
      rig = { yard: createYardScene(world), renderer, canvas, camera }
    } catch {
      rig = null
    }
    return rig
  }

  return {
    async gate(car) {
      const active = getRig()
      if (!active) return truthCapture.gate(car)

      const target = new THREE.Vector3(car.x, 0.8, car.y)
      const shots: Array<{ view: string; dir: THREE.Vector3 }> = [
        { view: GATE_VIEWS[0], dir: offset(car, GATE_CAMERA_DISTANCE, 0) },
        { view: GATE_VIEWS[1], dir: offset(car, -GATE_CAMERA_DISTANCE, 0) },
        { view: GATE_VIEWS[2], dir: offset(car, 0, GATE_CAMERA_DISTANCE) },
        { view: GATE_VIEWS[3], dir: offset(car, 0, -GATE_CAMERA_DISTANCE) },
      ]

      const frames: AiFrame[] = []
      for (const shot of shots) {
        active.camera.position.copy(target).add(shot.dir)
        active.camera.position.y = GATE_CAMERA_HEIGHT
        active.camera.lookAt(target)
        frames.push(frame(active, car, active.camera, shot.view))
      }
      return frames
    },

    async track(car, step) {
      const active = getRig()
      if (!active) return truthCapture.track(car, step)

      const target = new THREE.Vector3(car.x, 0.8, car.y)
      active.camera.position.copy(target).add(offset(car, 5, 4))
      active.camera.position.y = TRACK_CAMERA_HEIGHT
      active.camera.lookAt(target)
      return frame(active, car, active.camera, `track-${step}`)
    },
  }
}
