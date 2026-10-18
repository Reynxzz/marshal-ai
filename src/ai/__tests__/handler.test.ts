import { describe, expect, it, vi } from 'vitest'
import {
  type AiTaskDeps,
  handleAiTask,
  MISSING_KEY_MESSAGE,
  offlineEnabledFromEnv,
} from '../handler'

const payload = {
  frame: {
    view: 'front',
    mime: 'image/jpeg',
    dataUrl: '',
    width: 640,
    height: 400,
    truth: 'colour=red; headlights=on; brake_lights=off; workers=0',
  },
  buildSheet: { model: 'M', colour: 'blue', options: [] },
}

const unusedOnline = vi.fn(async () => {
  throw new Error('online must not be called')
})

function deps(overrides: Partial<AiTaskDeps> = {}): AiTaskDeps {
  return { offline: false, hasKey: true, runOnline: async () => ({}), ...overrides }
}

describe('handleAiTask', () => {
  it('parses the offline flag from env values', () => {
    expect(offlineEnabledFromEnv({})).toBe(false)
    expect(offlineEnabledFromEnv({ VITE_AI_OFFLINE: 'false' })).toBe(false)
    expect(offlineEnabledFromEnv({ VITE_AI_OFFLINE: 'true' })).toBe(true)
  })

  it('uses the offline handler only when offline is enabled', async () => {
    const res = await handleAiTask(
      'visual.match',
      payload,
      deps({ offline: true, hasKey: false, runOnline: unusedOnline }),
    )
    expect(res.ok).toBe(true)
    expect(res.degraded).toBe(true)
    expect((res.result as { mismatches: unknown[] }).mismatches).toHaveLength(1)
    expect(unusedOnline).not.toHaveBeenCalled()
  })

  it('errors instead of degrading when offline is disabled and no key is configured', async () => {
    const res = await handleAiTask(
      'visual.match',
      payload,
      deps({ offline: false, hasKey: false, runOnline: unusedOnline }),
    )
    expect(res).toEqual({ ok: false, degraded: false, error: MISSING_KEY_MESSAGE })
  })

  it('surfaces a model failure instead of falling back to offline', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const res = await handleAiTask(
        'visual.match',
        payload,
        deps({
          offline: false,
          hasKey: true,
          runOnline: async () => {
            throw new Error('model timeout')
          },
        }),
      )
      expect(res).toEqual({ ok: false, degraded: false, error: 'model timeout' })
    } finally {
      warn.mockRestore()
    }
  })

  it('returns the model result when online', async () => {
    const result = { mismatches: [], confidence: 0.9 }
    const res = await handleAiTask(
      'visual.match',
      payload,
      deps({ offline: false, hasKey: true, runOnline: async () => result }),
    )
    expect(res).toEqual({ ok: true, degraded: false, result })
  })
})
