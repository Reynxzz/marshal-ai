import { describe, expect, it } from 'vitest'
import { CASE_IDS, CASE_LIBRARY, caseById, sampleCaseIds, validateCaseIds } from '../batch/cases'
import { makeRng } from '../rng'

describe('case library', () => {
  it('has 20 unique, well-formed cases', () => {
    expect(CASE_LIBRARY).toHaveLength(20)
    expect(new Set(CASE_IDS).size).toBe(20)
    for (const c of CASE_LIBRARY) {
      expect(c.label.length).toBeGreaterThan(0)
      if (c.expected.kind === 'wrong_colour') {
        expect(c.declaredColour).toBeDefined()
        expect(c.spawnColour).toBeDefined()
      }
      if (c.expected.kind === 'dead_lamp') expect(c.expected.faultStep).toBeDefined()
      if (c.expected.kind === 'incident') expect(c.expected.blockers?.length).toBeGreaterThan(0)
      if (c.expected.kind === 'clean') {
        expect(c.declaredColour).toBeDefined()
        expect(c.spawnColour).toBeUndefined()
      }
      expect(caseById(c.id)).toBe(c)
    }
  })

  it('sampleCaseIds is deterministic, unique and caps incidents at one', () => {
    const a = sampleCaseIds(makeRng(7), 6)
    const b = sampleCaseIds(makeRng(7), 6)
    expect(a).toEqual(b)
    expect(new Set(a).size).toBe(6)
    for (let seed = 1; seed <= 50; seed++) {
      const ids = sampleCaseIds(makeRng(seed), 6)
      expect(new Set(ids).size).toBe(6)
      const incidents = ids.filter((id) => caseById(id)?.expected.kind === 'incident')
      expect(incidents.length).toBeLessThanOrEqual(1)
    }
  })

  it('validateCaseIds rejects unknown ids, duplicates, wrong counts and two incidents', () => {
    expect(validateCaseIds(['wc-blue-red', 'clean-blue'], 2)).toEqual(['wc-blue-red', 'clean-blue'])
    expect(validateCaseIds(['wc-blue-red', 'nope'], 2)).toBeNull()
    expect(validateCaseIds(['wc-blue-red', 'wc-blue-red'], 2)).toBeNull()
    expect(validateCaseIds(['wc-blue-red'], 2)).toBeNull()
    expect(validateCaseIds(['inc-box', 'inc-worker'], 2)).toBeNull()
    expect(validateCaseIds('wc-blue-red', 1)).toBeNull()
  })
})
