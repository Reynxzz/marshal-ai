import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { POINTS } from '#/lib/marshal/points'
import type { ParkingInput } from '#/lib/marshal/schemas'
import type { ParkingStep } from '#/lib/marshal/types'
import { generateJson, isGeminiConfigured, parseModelJson, runGemini } from '../gemini'
import { planParkingStep as parkingServerFn } from '../parking'

// vi.mock calls below are hoisted above these imports by vitest.

/** The mocked createServerFn returns a plain function of `{ data }`. */
type Handler = (ctx: { data: unknown }) => Promise<ParkingStep>
const planParkingStep = parkingServerFn as unknown as Handler

// The SDK is replaced by a fake whose constructor options and generateContent calls are recorded.
const sdk = vi.hoisted(() => ({
  options: [] as unknown[],
  generateContent: vi.fn(async (_request: unknown) => ({ text: '' as string | undefined })),
}))

vi.mock('@google/genai', () => ({
  GoogleGenAI: class {
    models = { generateContent: sdk.generateContent }
    constructor(options: unknown) {
      sdk.options.push(options)
    }
  },
}))

// createServerFn is replaced by a builder that runs the validator, then the handler.
vi.mock('@tanstack/react-start', () => ({
  createServerFn: () => {
    let validate: (input: unknown) => unknown = (input) => input
    const builder = {
      validator(fn: (input: unknown) => unknown) {
        validate = fn
        return builder
      },
      handler(fn: (ctx: { data: unknown }) => Promise<unknown>) {
        return ({ data }: { data: unknown }) => fn({ data: validate(data) })
      },
    }
    return builder
  },
}))

const ENV_KEYS = [
  'GOOGLE_GENAI_API_KEY',
  'GOOGLE_GENAI_USE_VERTEXAI',
  'GOOGLE_GENAI_USE_ENTERPRISE',
  'GOOGLE_CLOUD_PROJECT',
  'GOOGLE_CLOUD_LOCATION',
  'GEMINI_MODEL',
]

beforeEach(() => {
  for (const key of ENV_KEYS) vi.stubEnv(key, '')
  sdk.options.length = 0
  sdk.generateContent.mockReset()
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
})

describe('parseModelJson', () => {
  it('reads bare, fenced and prose-wrapped JSON', () => {
    expect(parseModelJson('{"a":1}')).toEqual({ a: 1 })
    expect(parseModelJson('```json\n{"a":{"b":[1,2]}}\n```')).toEqual({ a: { b: [1, 2] } })
    expect(parseModelJson('Sure! Here it is: {"route":"lane_c"} Hope that helps.')).toEqual({
      route: 'lane_c',
    })
  })

  it('throws when there is no JSON object', () => {
    expect(() => parseModelJson('no idea')).toThrow('model did not return JSON')
    expect(() => parseModelJson('} {')).toThrow()
  })
})

describe('runGemini', () => {
  it('answers offline without calling Gemini when nothing is configured', async () => {
    const online = vi.fn(async () => 'online')
    expect(isGeminiConfigured()).toBe(false)
    expect(await runGemini('t', () => 'offline', online)).toBe('offline')
    expect(online).not.toHaveBeenCalled()
  })

  it('uses the online answer when configured and falls back when it throws', async () => {
    vi.stubEnv('GOOGLE_GENAI_API_KEY', 'test-key')
    expect(
      await runGemini(
        't',
        () => 'offline',
        async () => 'online',
      ),
    ).toBe('online')
    const failing = async (): Promise<string> => {
      throw new Error('quota')
    }
    expect(await runGemini('t', () => 'offline', failing)).toBe('offline')
  })
})

describe('generateJson', () => {
  const schema = z.object({ route: z.string() })

  it('uses Vertex AI when GOOGLE_GENAI_USE_VERTEXAI is set', async () => {
    vi.stubEnv('GOOGLE_GENAI_USE_VERTEXAI', 'true')
    vi.stubEnv('GOOGLE_CLOUD_PROJECT', 'demo-project')
    vi.stubEnv('GOOGLE_CLOUD_LOCATION', 'asia-southeast1')
    vi.stubEnv('GEMINI_MODEL', 'gemini-test')
    sdk.generateContent.mockResolvedValue({ text: '```json\n{"route":"lane_c"}\n```' })
    const out = await generateJson('pick', ['data:image/png;base64,QUJD', 'REVG'], schema)
    expect(out).toEqual({ route: 'lane_c' })
    const vertex = { vertexai: true, project: 'demo-project', location: 'asia-southeast1' }
    expect(sdk.options).toEqual([vertex])
    const request = sdk.generateContent.mock.calls[0][0] as {
      model: string
      contents: { parts: unknown[] }[]
    }
    expect(request.model).toBe('gemini-test')
    expect(request.contents[0].parts).toEqual([
      { inlineData: { mimeType: 'image/png', data: 'QUJD' } },
      { inlineData: { mimeType: 'image/jpeg', data: 'REVG' } },
      { text: 'pick' },
    ])
  })

  it('uses the API key when Vertex is switched off, and rejects schema mismatches', async () => {
    vi.stubEnv('GOOGLE_GENAI_API_KEY', 'test-key')
    vi.stubEnv('GOOGLE_GENAI_USE_VERTEXAI', 'false')
    sdk.generateContent.mockResolvedValue({ text: '{"route":7}' })
    await expect(generateJson('pick', [], schema)).rejects.toThrow()
    expect(sdk.options).toEqual([{ apiKey: 'test-key' }])
  })
})

describe('planParkingStep handler', () => {
  const slot = { id: 'yard.Y5', ...POINTS['yard.Y5'] }
  const input = (pose: ParkingInput['pose']): ParkingInput => ({
    vin: 'V',
    pose,
    obstacles: [],
    slot,
  })

  it('rejects oversized inputs before any planning', async () => {
    const box = { id: 'o', x: 0, y: 0, yaw: 0, length: 1, width: 1, moving: false }
    const obstacles = Array.from({ length: 65 }, (_, i) => ({ ...box, id: `o${i}` }))
    const data = { ...input(POINTS['yard.entry']), obstacles }
    await expect(Promise.resolve().then(() => planParkingStep({ data }))).rejects.toThrow()
  })

  it('answers "parked" without Gemini once the car is in the slot', async () => {
    vi.stubEnv('GOOGLE_GENAI_API_KEY', 'test-key')
    const out = await planParkingStep({ data: input({ ...POINTS['yard.Y5'] }) })
    expect(out).toMatchObject({ decision: 'parked', degraded: false })
    expect(sdk.generateContent).not.toHaveBeenCalled()
  })

  it('lets Gemini pick an A* candidate and falls back to the cheapest path', async () => {
    vi.stubEnv('GOOGLE_GENAI_API_KEY', 'test-key')
    sdk.generateContent.mockResolvedValueOnce({ text: '{"decision":"path_b","confidence":0.8}' })
    const picked = await planParkingStep({ data: input(POINTS['yard.entry']) })
    expect(picked).toMatchObject({ decision: 'path_b', confidence: 0.8, degraded: false })
    expect(picked.path.length).toBeGreaterThan(0)

    sdk.generateContent.mockResolvedValueOnce({ text: 'I would rather not say' })
    const fallback = await planParkingStep({ data: input(POINTS['yard.entry']) })
    expect(fallback).toMatchObject({ decision: 'path_a', degraded: true })
  })
})
