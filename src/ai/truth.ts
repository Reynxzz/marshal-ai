// Truth capture: describes what the scene shows without rendering.
// Used offline, in unit tests, and as the fallback when WebGL is unavailable (T05).

import type { AiFrame } from '#/ai/tasks'
import { colourName } from '#/lib/colors'
import type { CapturePort } from '#/sim/ports'
import type { CarSnapshot } from '#/sim/vehicle'

const WIDTH = 640
const HEIGHT = 400

export function truthForCar(car: CarSnapshot): string {
  return [
    `colour=${colourName(car.colour)}`,
    `headlights=${car.lamps.headlights ? 'on' : 'off'}`,
    `brake_lights=${car.lamps.brake_lights ? 'on' : 'off'}`,
    'workers=0',
  ].join('; ')
}

function truthFrame(car: CarSnapshot, view: string): AiFrame {
  return {
    view,
    mime: 'image/jpeg',
    dataUrl: '',
    width: WIDTH,
    height: HEIGHT,
    truth: truthForCar(car),
  }
}

export const GATE_VIEWS = ['front', 'rear', 'left', 'right'] as const

export const truthCapture: CapturePort = {
  async gate(car) {
    return GATE_VIEWS.map((v) => truthFrame(car, v))
  },
  async track(car, step) {
    return truthFrame(car, `track-${step}`)
  },
}
