// Server-only Gemini helper for the Marshal server functions. Together with src/ai/server.ts this
// is the ONLY module allowed to read GOOGLE_GENAI_* / GOOGLE_CLOUD_* / GEMINI_MODEL. It is only
// ever loaded lazily from inside createServerFn handlers, so it never reaches the browser bundle.

try {
  process.loadEnvFile?.()
} catch {
  // Ignore if .env is not present or already loaded
}

import { GoogleGenAI, type Part } from '@google/genai'
import type { z } from 'zod'

const MODEL_TIMEOUT_MS = 15_000

/**
 * Same rule as src/ai/server.ts: an API key or a Vertex AI switch enables online mode.
 * @returns True when Gemini can be called.
 */
export function isGeminiConfigured(): boolean {
  const apiKey = process.env.GOOGLE_GENAI_API_KEY ?? ''
  return apiKey.trim() !== '' || vertexFlag().trim() !== ''
}

function vertexFlag(): string {
  return process.env.GOOGLE_GENAI_USE_VERTEXAI ?? process.env.GOOGLE_GENAI_USE_ENTERPRISE ?? ''
}

/**
 * @returns The Gemini model name (GEMINI_MODEL, default gemini-2.5-flash).
 */
export function geminiModel(): string {
  return process.env.GEMINI_MODEL ?? 'gemini-2.5-flash'
}

function createClient(): GoogleGenAI {
  const apiKey = (process.env.GOOGLE_GENAI_API_KEY ?? '').trim()
  const vertex = vertexFlag().trim().toLowerCase()
  const useVertex = vertex !== '' && !['0', 'false', 'no'].includes(vertex)
  if (useVertex) {
    return new GoogleGenAI({
      vertexai: true,
      project: process.env.GOOGLE_CLOUD_PROJECT,
      location: process.env.GOOGLE_CLOUD_LOCATION ?? 'us-central1',
    })
  }
  return new GoogleGenAI({ apiKey })
}

/**
 * Turns a base64 JPEG (bare or data URL) into an inline image part.
 * @param image - base64 JPEG.
 * @returns Gemini inline data part.
 */
function imagePart(image: string): Part {
  const match = /^data:([^;,]+);base64,(.*)$/s.exec(image)
  return {
    inlineData: { mimeType: match ? match[1] : 'image/jpeg', data: match ? match[2] : image },
  }
}

/**
 * Extracts the first JSON object from model text (tolerates code fences).
 * @param text - Raw model text.
 * @returns Parsed JSON.
 */
export function parseModelJson(text: string): unknown {
  const fenced = text.replace(/```(?:json)?/gi, '').trim()
  const start = fenced.indexOf('{')
  const end = fenced.lastIndexOf('}')
  if (start < 0 || end < start) throw new Error('model did not return JSON')
  return JSON.parse(fenced.slice(start, end + 1))
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`gemini call timed out after ${ms}ms`)), ms)
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

/**
 * Sends a prompt plus base64 JPEG frames to Gemini, requests JSON and validates it with `schema`.
 * Rejects on timeout (15s), transport errors, non-JSON output or schema mismatch.
 * @param prompt - Instruction text, including the JSON shape to return.
 * @param images - base64 JPEG frames (bare or data URLs), sent before the text.
 * @param schema - zod schema for the expected JSON.
 * @returns The parsed, schema-valid answer.
 */
export async function generateJson<T>(
  prompt: string,
  images: readonly string[],
  schema: z.ZodType<T>,
): Promise<T> {
  const ai = createClient()
  const parts: Part[] = [...images.map(imagePart), { text: prompt }]
  const response = await withTimeout(
    ai.models.generateContent({
      model: geminiModel(),
      contents: [{ role: 'user', parts }],
      config: { responseMimeType: 'application/json' },
    }),
    MODEL_TIMEOUT_MS,
  )
  return schema.parse(parseModelJson(response.text ?? ''))
}

/**
 * Runs `online` when Gemini is configured, else (or on any error / timeout) `offline`.
 * Never throws.
 * @param label - Log label.
 * @param offline - Deterministic answer (flags `degraded: true`).
 * @param online - Gemini-backed answer.
 * @returns The online answer, or the offline one.
 */
export async function runGemini<T>(
  label: string,
  offline: () => T,
  online: () => Promise<T>,
): Promise<T> {
  if (!isGeminiConfigured()) return offline()
  try {
    return await online()
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    console.warn(`[${label}] falling back to offline:`, message)
    return offline()
  }
}
