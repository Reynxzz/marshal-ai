// Browser-safe AI client. Wraps the aiTask server function with a timeout and one retry on
// network error. When VITE_AI_OFFLINE=true it falls back to the deterministic offline handlers;
// otherwise a failed call throws (Gemini is forced, nothing degrades silently).
// Never imports @google/adk and never reads env keys.

import { offlineAllowed } from './mode'
import { offlineTask } from './offline'
import type { AiTaskResult } from './server'
import {
  type AiTaskFn,
  type AiTaskInput,
  type AiTaskName,
  type AiTaskOutput,
  aiTasks,
} from './tasks'

export type AiCallMeta = {
  task: AiTaskName
  latencyMs: number
  degraded: boolean
  error?: string
}

export type AiSend = (args: { task: AiTaskName; payload: unknown }) => Promise<AiTaskResult>

export type CreateAiClientOptions = {
  timeoutMs?: number
  send?: AiSend
  fetchFn?: AiSend
}

const listeners = new Set<(meta: AiCallMeta) => void>()

/** Subscribe to per-call metadata. Returns an unsubscribe function. */
export function onAiCall(callback: (meta: AiCallMeta) => void): () => void {
  listeners.add(callback)
  return () => {
    listeners.delete(callback)
  }
}

function emit(meta: AiCallMeta): void {
  for (const callback of listeners) callback(meta)
}

const defaultSend: AiSend = async (args) => {
  const { aiTask } = await import('./server')
  return aiTask({ data: args })
}

export function createAiClient(options: CreateAiClientOptions = {}): AiTaskFn {
  const send = options.send ?? options.fetchFn ?? defaultSend
  const timeoutMs = options.timeoutMs ?? 20_000

  return async function aiTaskFn<K extends AiTaskName>(
    task: K,
    payload: AiTaskInput<K>,
  ): Promise<AiTaskOutput<K>> {
    const startedAt = Date.now()
    let envelope: AiTaskResult | undefined
    let error: unknown

    // One retry on network error (send throwing); a resolved-but-invalid envelope does not retry.
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        envelope = await withTimeout(send({ task, payload }), timeoutMs)
        break
      } catch (sendError) {
        error = sendError
      }
    }

    if (envelope?.ok && envelope.result !== undefined) {
      try {
        const parsed = aiTasks[task].output.parse(envelope.result) as AiTaskOutput<K>
        emit({ task, latencyMs: Date.now() - startedAt, degraded: envelope.degraded === true })
        return parsed
      } catch (schemaError) {
        error = schemaError
      }
    } else if (envelope && !envelope.ok) {
      error = new Error(envelope.error ?? 'ai task failed')
    }

    if (!offlineAllowed()) {
      throw error instanceof Error
        ? error
        : new Error('ai task failed (offline AI disabled; set VITE_AI_OFFLINE=true to allow it)')
    }

    const fallback = offlineTask(task, payload)
    emit({
      task,
      latencyMs: Date.now() - startedAt,
      degraded: true,
      error: error === undefined ? undefined : errorMessage(error),
    })
    return fallback
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`ai call timed out after ${ms}ms`)), ms)
    promise.then(
      (value) => {
        clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        clearTimeout(timer)
        reject(error)
      },
    )
  })
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
