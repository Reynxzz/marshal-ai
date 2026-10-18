import { describe, expect, it } from 'vitest'
import { minimapViewport } from '../viewer'

describe('minimapViewport', () => {
  it('sits in the bottom-right corner with a margin', () => {
    const rect = minimapViewport(1000, 600)
    expect(rect.width).toBe(rect.height)
    expect(rect.width).toBeLessThanOrEqual(220)
    expect(rect.width).toBeGreaterThan(100)
    expect(rect.x).toBe(1000 - rect.width - 12)
    expect(rect.y).toBe(12)
  })

  it('keeps a minimum size on small canvases', () => {
    const rect = minimapViewport(200, 150)
    expect(rect.width).toBe(80)
    expect(rect.x).toBe(200 - rect.width - 12)
    expect(rect.y).toBe(12)
  })
})
