// Pure decision logic behind the aiTask server function: offline handlers run ONLY when
// VITE_AI_OFFLINE is set, otherwise Gemini is required and any failure surfaces as an error
// instead of being hidden as an offline answer. Kept free of the Start runtime so it can be
// unit tested directly.

import { AI_OFFLINE_ENV, isOfflineValue } from './mode'
import { offlineTask } from './offline'
import { type AiTaskName, validateTask } from './tasks'

export type AiTaskResult = {
  ok: boolean
  degraded: boolean
  result?: unknown
  error?: string
}

export type AiTaskDeps = {
  offline: boolean
  hasKey: boolean
  runOnline: (task: AiTaskName, payload: unknown) => Promise<unknown>
}

export const MISSING_KEY_MESSAGE =
  'Gemini is required (offline AI is disabled): set GOOGLE_GENAI_API_KEY or ' +
  `GOOGLE_GENAI_USE_VERTEXAI in .env, or set ${AI_OFFLINE_ENV}=true to use offline AI.`

export function offlineEnabledFromEnv(env: Record<string, string | undefined>): boolean {
  return isOfflineValue(env[AI_OFFLINE_ENV])
}

export async function handleAiTask(
  task: AiTaskName,
  rawPayload: unknown,
  deps: AiTaskDeps,
): Promise<AiTaskResult> {
  try {
    const payload = validateTask(task, rawPayload)
    if (deps.offline) return { ok: true, degraded: true, result: offlineTask(task, payload) }
    if (!deps.hasKey) return { ok: false, degraded: false, error: MISSING_KEY_MESSAGE }
    try {
      return { ok: true, degraded: false, result: await deps.runOnline(task, payload) }
    } catch (error) {
      console.warn(`[aiTask:${task}] gemini failed (offline disabled):`, errorMessage(error))
      return { ok: false, degraded: false, error: errorMessage(error) }
    }
  } catch (error) {
    if (!deps.offline) return { ok: false, degraded: false, error: errorMessage(error) }
    try {
      return {
        ok: true,
        degraded: true,
        result: offlineTask(task, rawPayload as never),
        error: errorMessage(error),
      }
    } catch (fallbackError) {
      return { ok: false, degraded: true, error: errorMessage(fallbackError) }
    }
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
