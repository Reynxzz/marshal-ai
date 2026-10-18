import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type AiCallMeta, type AiSend, createAiClient, onAiCall } from '../client'
import { offlineTask } from '../offline'
import type { AiFrame } from '../tasks'

const frame: AiFrame = {
  view: 'front',
  mime: 'image/jpeg',
  dataUrl: 'data:image/jpeg;base64,AAAA',
  width: 640,
  height: 400,
  truth: 'colour=red; headlights=off; brake_lights=off; workers=0',
}

const input = { frame, buildSheet: { model: 'M', colour: 'blue' as const, options: [] } }

describe('createAiClient (offline allowed)', () => {
  beforeEach(() => vi.stubEnv('VITE_AI_OFFLINE', 'true'))
  afterEach(() => vi.unstubAllEnvs())

  it('falls back to offline and marks degraded when send rejects', async () => {
    const send: AiSend = vi.fn(async () => {
      throw new Error('network down')
    })
    const metas: AiCallMeta[] = []
    const unsubscribe = onAiCall((meta) => metas.push(meta))
    try {
      const client = createAiClient({ send, timeoutMs: 50 })
      const out = await client('visual.match', input)
      expect(out).toEqual(await offlineTask('visual.match', input))
      expect(send).toHaveBeenCalledTimes(2)
      expect(metas.at(-1)?.degraded).toBe(true)
      expect(metas.at(-1)?.task).toBe('visual.match')
    } finally {
      unsubscribe()
    }
  })

  it('falls back to offline when send returns an invalid envelope', async () => {
    const send: AiSend = async () => ({ ok: false, degraded: true, error: 'boom' })
    const client = createAiClient({ send })
    const out = await client('visual.match', input)
    expect(out).toEqual(await offlineTask('visual.match', input))
  })

  it('falls back to offline when the result fails the output schema', async () => {
    const send: AiSend = async () => ({ ok: true, degraded: false, result: { nope: true } })
    const client = createAiClient({ send })
    const out = await client('visual.match', input)
    expect(out).toEqual(await offlineTask('visual.match', input))
  })

  it('returns a valid result unchanged and reports non-degraded metadata', async () => {
    const result = {
      mismatches: [{ field: 'colour', declared: 'blue', seen: 'red' }],
      confidence: 0.91,
    }
    const send: AiSend = async () => ({ ok: true, degraded: false, result })
    const metas: AiCallMeta[] = []
    const unsubscribe = onAiCall((meta) => metas.push(meta))
    try {
      const client = createAiClient({ send })
      await expect(client('visual.match', input)).resolves.toEqual(result)
      expect(metas.at(-1)).toMatchObject({ task: 'visual.match', degraded: false })
      expect(typeof metas.at(-1)?.latencyMs).toBe('number')
    } finally {
      unsubscribe()
    }
  })

  it('stops reporting after unsubscribe', async () => {
    const send: AiSend = async () => ({
      ok: true,
      degraded: false,
      result: { mismatches: [], confidence: 1 },
    })
    const callback = vi.fn()
    const unsubscribe = onAiCall(callback)
    unsubscribe()
    await createAiClient({ send })('visual.match', input)
    expect(callback).not.toHaveBeenCalled()
  })
})

describe('createAiClient (Gemini forced, offline disabled)', () => {
  beforeEach(() => vi.unstubAllEnvs())

  it('throws when send rejects instead of degrading', async () => {
    const send: AiSend = vi.fn(async () => {
      throw new Error('network down')
    })
    const client = createAiClient({ send, timeoutMs: 50 })
    await expect(client('visual.match', input)).rejects.toThrow('network down')
  })

  it('surfaces a server error envelope', async () => {
    const send: AiSend = async () => ({ ok: false, degraded: false, error: 'gemini exploded' })
    const client = createAiClient({ send })
    await expect(client('visual.match', input)).rejects.toThrow('gemini exploded')
  })

  it('throws when the result fails the output schema', async () => {
    const send: AiSend = async () => ({ ok: true, degraded: false, result: { nope: true } })
    const client = createAiClient({ send })
    await expect(client('visual.match', input)).rejects.toThrow()
  })
})
