// Server-only AI entry point. Wraps the ADK agents behind a TanStack Start server function so
// the browser can call it via RPC. Offline handlers are used ONLY when VITE_AI_OFFLINE=true is
// set in .env; otherwise Gemini is forced and failures are returned as errors, never hidden as
// offline answers. This module and src/lib/serverFns/gemini.ts are the ONLY modules allowed to
// read GOOGLE_GENAI_API_KEY / GOOGLE_GENAI_USE_VERTEXAI.

try {
  process.loadEnvFile?.()
} catch {
  // Ignore if .env is not present or already loaded
}

import { createServerFn } from '@tanstack/react-start'
import { type AiTaskResult, handleAiTask, offlineEnabledFromEnv } from './handler'
import { type AiTaskName, aiTasks } from './tasks'

export type { AiTaskResult } from './handler'

const MODEL_TIMEOUT_MS = 15_000

function isKeyConfigured(): boolean {
  const apiKey = process.env.GOOGLE_GENAI_API_KEY ?? ''
  const vertex =
    process.env.GOOGLE_GENAI_USE_VERTEXAI ?? process.env.GOOGLE_GENAI_USE_ENTERPRISE ?? ''
  return apiKey.trim() !== '' || vertex.trim() !== ''
}

function modelName(): string {
  return process.env.GEMINI_MODEL ?? 'gemini-2.5-flash'
}

async function runOnline(task: AiTaskName, payload: unknown): Promise<unknown> {
  const { runTaskAgent } = await import('./agents/specialists')
  const result = await withTimeout(runTaskAgent(task, payload, modelName()), MODEL_TIMEOUT_MS)
  return aiTasks[task].output.parse(result)
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`ai task timed out after ${ms}ms`)), ms)
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

export const aiTask = createServerFn({ method: 'POST', strict: false })
  .validator((input: unknown): { task: AiTaskName; payload: unknown } => {
    const request = input as { task?: unknown; payload?: unknown } | null
    if (!request || typeof request !== 'object') throw new Error('aiTask: invalid request')
    return { task: request.task as AiTaskName, payload: request.payload }
  })
  .handler(
    ({ data }): Promise<AiTaskResult> =>
      handleAiTask(data.task, data.payload, {
        offline: offlineEnabledFromEnv(process.env),
        hasKey: isKeyConfigured(),
        runOnline,
      }),
  )
