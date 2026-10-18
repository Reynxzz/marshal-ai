// Client-side frame trimming. The server functions reject requests over the schema limits
// (MAX_FRAMES images, MAX_FRAME_CHARS per image), so the flows fit their frames first instead of
// losing the whole Gemini call to a validation error. Pure module.

import { MAX_FRAME_CHARS, MAX_FRAMES } from './schemas'
import type { GateFrames, GateView } from './types'

/** Frames that fit one request, plus how many were dropped. */
export type FittedFrames = { frames: string[]; oversized: number; overflow: number }

/**
 * Fits a frame sequence to the schema limits: frames over {@link MAX_FRAME_CHARS} are dropped,
 * then the most recent {@link MAX_FRAMES} are kept.
 * @param frames - base64 JPEG frames, oldest first.
 * @returns The frames to send (still oldest first) and the drop counts.
 */
export function fitFrames(frames: readonly string[]): FittedFrames {
  const small = frames.filter((frame) => frame.length <= MAX_FRAME_CHARS)
  const kept = small.slice(Math.max(0, small.length - MAX_FRAMES))
  return {
    frames: kept,
    oversized: frames.length - small.length,
    overflow: small.length - kept.length,
  }
}

/**
 * Drops gate views whose frame is over {@link MAX_FRAME_CHARS}.
 * @param frames - One base64 JPEG per gate camera.
 * @returns The views that fit and the dropped view names.
 */
export function fitGateFrames(frames: Partial<GateFrames>): {
  frames: Partial<GateFrames>
  dropped: GateView[]
} {
  const kept: Partial<GateFrames> = {}
  const dropped: GateView[] = []
  for (const [view, frame] of Object.entries(frames) as [GateView, string | undefined][]) {
    if (frame === undefined) continue
    if (frame.length <= MAX_FRAME_CHARS) kept[view] = frame
    else dropped.push(view)
  }
  return { frames: kept, dropped }
}
