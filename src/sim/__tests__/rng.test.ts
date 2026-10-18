import { describe, expect, it } from 'vitest'
import { makeRng } from '../rng'

describe('makeRng', () => {
  it('is deterministic for a given seed', () => {
    const a = makeRng(7)
    const b = makeRng(7)
    for (let i = 0; i < 20; i++) {
      expect(a.next()).toBe(b.next())
    }
  })

  it('int/pick/sample/bool stay in range and are deterministic', () => {
    const a = makeRng(42)
    const b = makeRng(42)
    for (let i = 0; i < 50; i++) {
      const n = a.int(6)
      expect(n).toBeGreaterThanOrEqual(0)
      expect(n).toBeLessThan(6)
      expect(n).toBe(b.int(6))
    }
    expect(a.pick(['x', 'y', 'z'])).toBe(b.pick(['x', 'y', 'z']))
    expect(a.sample([1, 2, 3, 4, 5], 3)).toEqual(b.sample([1, 2, 3, 4, 5], 3))
    expect(a.bool(0.5)).toBe(b.bool(0.5))
  })

  it('sample never mutates the source array', () => {
    const rng = makeRng(1)
    const src = [1, 2, 3, 4]
    rng.sample(src, 3)
    expect(src).toEqual([1, 2, 3, 4])
  })
})
