// Seeded deterministic RNG (mulberry32). Every random draw in the simulation must go
// through this so a batch can be replayed from its seed.

export type Rng = {
  next(): number
  int(max: number): number
  pick<T>(arr: readonly T[]): T
  sample<T>(arr: readonly T[], n: number): T[]
  bool(p: number): boolean
}

export function makeRng(seed: number): Rng {
  let a = seed >>> 0

  const next = (): number => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  const int = (max: number): number => Math.floor(next() * max)

  const pick = <T>(arr: readonly T[]): T => {
    if (arr.length === 0) throw new Error('pick from empty array')
    return arr[int(arr.length)] as T
  }

  const sample = <T>(arr: readonly T[], n: number): T[] => {
    const copy = arr.slice()
    const take = Math.min(n, copy.length)
    for (let i = 0; i < take; i++) {
      const j = i + int(copy.length - i)
      const tmp = copy[i] as T
      copy[i] = copy[j] as T
      copy[j] = tmp
    }
    return copy.slice(0, take)
  }

  const bool = (p: number): boolean => next() < p

  return { next, int, pick, sample, bool }
}
