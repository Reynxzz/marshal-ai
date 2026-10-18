// Injectable ports so the pure simulation never imports three.js or React.
// The orchestrator wires the real implementations; tests wire the truth/offline ones.

import type { AiFrame } from '#/ai/tasks'
import type { CarSnapshot } from './vehicle'

export type CapturePort = {
  /** 4 fixed gate views (front/rear/left/right) of a car stopped at the inspection gate. */
  gate(car: CarSnapshot): Promise<AiFrame[]>
  /** One 3/4 front view of a car on the test track at a given routine step. */
  track(car: CarSnapshot, step: string): Promise<AiFrame>
}
