import { afterEach, describe, expect, it, vi } from 'vitest'
import { isOfflineValue, offlineAllowed } from '../mode'

describe('AI mode flag', () => {
  afterEach(() => vi.unstubAllEnvs())

  it('parses truthy values', () => {
    for (const v of ['1', 'true', 'TRUE', ' yes ', 'on']) expect(isOfflineValue(v)).toBe(true)
    for (const v of [undefined, '', '0', 'false', 'off', 'no'])
      expect(isOfflineValue(v)).toBe(false)
  })

  it('defaults to Gemini forced', () => {
    expect(offlineAllowed()).toBe(false)
  })

  it('enables offline when VITE_AI_OFFLINE is set', () => {
    vi.stubEnv('VITE_AI_OFFLINE', 'true')
    expect(offlineAllowed()).toBe(true)
  })
})
