// Shared services the flow modules use. Implemented by the BatchRunner (T13).
// Keeps flows free of direct engine/AI wiring so they are testable in isolation.

import type { AiTaskFn } from '#/ai/tasks'
import type { SimEngine } from '../engine'
import type { CapturePort } from '../ports'
import type { Level } from '../records'
import type { Rng } from '../rng'
import type { World } from '../world'

export type FlowEvent = {
  flow?: number
  source: string
  title: string
  detail: string
  level: Level
  payload?: unknown
}

export type FlowHost = {
  engine: SimEngine
  world: World
  ai: AiTaskFn
  capture: CapturePort
  rng: Rng
  batchId: string
  emit(ev: FlowEvent): void
}
