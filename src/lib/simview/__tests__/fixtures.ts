import {
  type AssistCase,
  type CarState,
  createInitialState,
  type MarshalState,
} from '#/lib/marshal/state'

export function car(
  vin: string,
  stage: CarState['stage'],
  extra: Partial<CarState> = {},
): CarState {
  return {
    vin,
    colour: 'blue',
    expectedColour: 'blue',
    stage,
    location: 'line_end',
    decisions: [],
    ...extra,
  }
}

export function openCase(case_id: string, vin: string, opened_at: number): AssistCase {
  return {
    case_id,
    vin,
    reason: 'Box in the lane',
    options: ['wait_for_removal', 'pass_left_3kmh'],
    frames: [],
    status: 'open',
    opened_at,
  }
}

export function stateWith(cars: CarState[], cases: AssistCase[] = []): MarshalState {
  const base = createInitialState(true)
  return {
    ...base,
    cars: Object.fromEntries(cars.map((item) => [item.vin, item])),
    order: cars.map((item) => item.vin),
    cases: Object.fromEntries(cases.map((item) => [item.case_id, item])),
  }
}
