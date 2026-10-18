import { describe, expect, it } from 'vitest'
import {
  autoFocusKey,
  autoFocusKeys,
  chooseFocus,
  focusAriaLabel,
  focusCaption,
  focusColour,
} from '../focus'
import { car, openCase, stateWith } from './fixtures'

const state = stateWith(
  [
    car('P001', 'test_track', { location: 'test_track' }),
    car('P002', 'waiting', { location: 'finishing' }),
    car('P003', 'waiting', { location: 'lane_c' }),
    car('P004', 'held', { location: 'report_point', held: 'yard full' }),
    car('P005', 'shipped'),
  ],
  [openCase('AC-P003-1', 'P003', 30), openCase('AC-P002-1', 'P002', 20)],
)

describe('chooseFocus', () => {
  it('focuses the oldest open case when nothing is selected', () => {
    expect(chooseFocus(state, { selected: null })).toEqual({
      vin: 'P002',
      reason: 'case',
      caseId: 'AC-P002-1',
      location: 'finishing',
      picked: false,
    })
  })

  it('prefers the selected car and tags it by what it waits on', () => {
    expect(chooseFocus(state, { selected: 'P001' })).toMatchObject({
      vin: 'P001',
      reason: 'selected',
      picked: true,
    })
    expect(chooseFocus(state, { selected: 'P003' })).toMatchObject({
      reason: 'case',
      caseId: 'AC-P003-1',
    })
    expect(chooseFocus(state, { selected: 'P004' })).toMatchObject({ reason: 'held' })
  })

  it('prefers a case picked with "Show in 3D" while it stays open', () => {
    expect(chooseFocus(state, { selected: 'P001', focusedCase: 'AC-P003-1' })).toMatchObject({
      vin: 'P003',
      reason: 'case',
      picked: true,
    })
    const decided = {
      ...state,
      cases: {
        ...state.cases,
        'AC-P003-1': { ...state.cases['AC-P003-1'], status: 'decided' as const },
      },
    }
    expect(chooseFocus(decided, { selected: 'P001', focusedCase: 'AC-P003-1' })?.vin).toBe('P001')
  })

  it('falls back to held cars, skips dismissed targets and returns null for the overview', () => {
    const dismissedCases = [autoFocusKey('case', 'AC-P002-1'), autoFocusKey('case', 'AC-P003-1')]
    expect(chooseFocus(state, { selected: null, dismissed: dismissedCases })).toMatchObject({
      vin: 'P004',
      reason: 'held',
    })
    expect(chooseFocus(state, { selected: null, dismissed: autoFocusKeys(state) })).toBeNull()
    expect(autoFocusKeys(state)).toEqual(['case:AC-P002-1', 'case:AC-P003-1', 'held:P004'])
  })

  it('ignores shipped and unknown cars', () => {
    const quiet = stateWith([car('P005', 'shipped'), car('P006', 'to_gate')])
    expect(chooseFocus(quiet, { selected: 'P005' })).toBeNull()
    expect(chooseFocus(quiet, { selected: 'NOPE' })).toBeNull()
  })
})

describe('focus text', () => {
  it('captions the focus and the overview', () => {
    const focus = chooseFocus(state, { selected: null })
    expect(focusCaption(focus, state)).toBe('Focused: P002 at finishing — waiting on you')
    expect(focusCaption(chooseFocus(state, { selected: 'P001' }), state)).toBe(
      'Focused: P001 at test_track — selected',
    )
    expect(focusCaption(null, state)).toBe('Overview: 4 cars on the plant')
    expect(focusAriaLabel(focus, state)).toBe(
      '3D plant view following car P002 at finishing, waiting on you.',
    )
    expect(focusAriaLabel(null, state)).toBe('3D plant view. Overview: 4 cars on the plant.')
  })

  it('uses amber for decisions and blue for a plain selection', () => {
    expect(focusColour({ reason: 'case' })).toBe('#E39B2E')
    expect(focusColour({ reason: 'held' })).toBe('#E39B2E')
    expect(focusColour({ reason: 'selected' })).toBe('#7FA7FF')
  })
})
