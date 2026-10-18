import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { colors, fonts, tokenCssVars } from '#/lib/designTokens'
import type { AssistCase, CarState, FeedEntry, Kpis, Truck } from '#/lib/marshal/state'
import { createOfflineMarshalAi } from '#/lib/marshal/testing/offlineAi'
import { ScriptedDrivingSystem } from '#/lib/marshal/testing/scriptedSystem'
import type { AssistChooseCmd } from '#/lib/marshal/types'
import { DEFAULT_SCENARIO, defaultShiftAi, getShiftBackend, useShiftStore } from '#/stores/shift'
import { CarDetail } from '../CarDetail'
import { Dashboard } from '../Dashboard'
import { DecisionFeed } from '../DecisionFeed'
import { Dock } from '../Dock'
import { GateLanes } from '../GateLanes'
import { Header } from '../Header'
import { KpiBar } from '../KpiBar'
import { ProgressBar } from '../ProgressBar'
import { WaitingOnYou } from '../WaitingOnYou'
import { Yard } from '../Yard'

function car(vin: string, stage: CarState['stage'], extra: Partial<CarState> = {}): CarState {
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

async function flush(): Promise<void> {
  for (let i = 0; i < 50; i++) await Promise.resolve()
}

const kpis: Kpis = { offLine: 12, decisions: 40, neededYou: 2, inYard: 7, shipped: 3, rework: 1 }

describe('design tokens', () => {
  it('match TEAM_PLAN M8 and are exposed as CSS custom properties', () => {
    expect(colors).toEqual({
      bg: '#12171D',
      surface: '#1B222B',
      surfaceAlt: '#151B22',
      border: '#2A3340',
      borderAlt: '#344052',
      text: '#E9E6DE',
      textMuted: '#A9B1BC',
      textDim: '#8C95A1',
      accent: '#E39B2E',
      blue: '#7FA7FF',
      blueLight: '#B8CCFF',
      sand: '#D9D5CB',
      gold: '#F6C77A',
      rework: '#C2621F',
    })
    expect(fonts).toEqual({
      ui: "'IBM Plex Sans', sans-serif",
      mono: "'IBM Plex Mono', monospace",
      head: "'Space Grotesk', sans-serif",
    })
    const vars = tokenCssVars()
    expect(vars['--md-blue-light']).toBe('#B8CCFF')
    expect(vars['--md-font-mono']).toBe(fonts.mono)
  })
})

describe('shift dashboard components', () => {
  it('KpiBar renders 6 labelled values', () => {
    render(<KpiBar kpis={kpis} totalCars={20} byTag={{ GEMINI: 30, YARD: 6 }} />)
    const labels = [
      ['Off the line', '12'],
      ['Decisions made', '40'],
      ['Needed you', '2'],
      ['In the yard', '7 / 80'],
      ['Shipped', '3'],
      ['Rework', '1'],
    ]
    expect(screen.getAllByRole('term')).toHaveLength(6)
    for (const [label, value] of labels) {
      const tile = screen.getByText(label).parentElement as HTMLElement
      expect(within(tile).getByText(value)).toHaveClass('md-kpi-value')
    }
    expect(screen.getByText('of 20 on this shift')).toBeInTheDocument()
    expect(screen.getByText('30 by Gemini · 6 by yard rules')).toBeInTheDocument()
    expect(screen.getByText('95% handled without you')).toBeInTheDocument()
  })

  it('ProgressBar sets width and aria from progress', () => {
    const { container, rerender } = render(<ProgressBar progress={0.42} />)
    const bar = screen.getByRole('progressbar', { name: 'Shift progress' })
    expect(bar).toHaveAttribute('aria-valuenow', '42')
    const fill = container.querySelector('.md-progress-fill') as HTMLElement
    expect(fill.style.width).toBe('42%')
    rerender(<ProgressBar progress={3} />)
    expect(bar).toHaveAttribute('aria-valuenow', '100')
    expect(fill.style.width).toBe('100%')
  })

  it('GateLanes groups cars by stage and chip click selects the car', () => {
    const onSelect = vi.fn()
    const cars = [
      car('P001', 'at_gate'),
      car('P002', 'to_slot', { slot: 'A2' }),
      car('P003', 'waiting'),
      car('P004', 'rework'),
      car('P005', 'parked', { slot: 'A1' }),
      car('P006', 'test_track'),
      car('P007', 'held', { slot: 'A4', held: 'Parking Y4 abandoned after 2 attempts' }),
      car('P008', 'held', { held: 'No free yard slot' }),
    ]
    render(<GateLanes cars={cars} selected="P002" onSelect={onSelect} />)
    const group = (name: string) => screen.getByRole('region', { name })
    const vins = (name: string) =>
      within(group(name))
        .queryAllByRole('button')
        .map((button) => button.textContent)
    expect(vins('At the gate')).toEqual(['P001', 'P006'])
    expect(vins('Driving to a slot')).toEqual(['P002'])
    // Held cars (parking abandoned, yard full) are the yard crew's, not "Waiting on you".
    expect(vins('Waiting on you')).toEqual(['P003'])
    expect(vins('Held for the yard crew')).toEqual(['P007', 'P008'])
    expect(screen.getByRole('button', { name: 'P007, Held for the yard crew' })).toHaveClass(
      'chip',
      'held',
    )
    expect(vins('Rework bay')).toEqual(['P004'])
    expect(screen.queryByRole('button', { name: /P005/ })).toBeNull()

    expect(within(group('At the gate')).getByRole('button', { name: /P001/ })).toHaveClass(
      'chip',
      'gate',
    )
    const lane = screen.getByRole('button', { name: 'P002, Driving to A2' })
    expect(lane).toHaveClass('chip', 'lane', 'sel')
    expect(lane).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /P003/ })).toHaveClass('chip', 'alert')
    expect(screen.getByRole('button', { name: /P004/ })).toHaveClass('chip', 'rework')

    fireEvent.click(screen.getByRole('button', { name: /P003/ }))
    expect(onSelect).toHaveBeenCalledWith('P003')
  })

  it('Yard renders 80 cells with state classes and A-H labels', () => {
    const onSelect = vi.fn()
    render(
      <Yard
        cells={{ A1: 'parked', A2: 'fresh', A3: 'res', A4: 'alert' }}
        occupants={{ A1: 'P001', A2: 'P002', A3: 'P003', A4: 'P004' }}
        selected="P001"
        onSelect={onSelect}
      />,
    )
    const cells = screen.getAllByRole('button', { name: /^Slot / })
    expect(cells).toHaveLength(80)
    expect(screen.getByRole('button', { name: 'Slot A1, P001, parked' })).toHaveClass(
      'cell',
      'parked',
      'sel',
    )
    expect(screen.getByRole('button', { name: 'Slot A2, P002, just parked' })).toHaveClass('fresh')
    expect(screen.getByRole('button', { name: 'Slot A3, P003, car on its way' })).toHaveClass('res')
    expect(screen.getByRole('button', { name: 'Slot A4, P004, needs you' })).toHaveClass('alert')
    const empty = screen.getByRole('button', { name: 'Slot H10, empty' })
    expect(empty).toHaveClass('cell', 'empty')
    expect(empty).toBeDisabled()
    expect(screen.getByRole('heading', { name: 'Yard · 4 of 80 slots in use' })).toBeVisible()
    for (const row of 'ABCDEFGH') expect(screen.getByText(row)).toHaveClass('md-yard-row')

    fireEvent.click(screen.getByRole('button', { name: /Slot A3/ }))
    expect(onSelect).toHaveBeenCalledWith('P003')
  })

  it('Dock renders truck state classes', () => {
    const trucks: Truck[] = [
      { id: 'T-1', state: 'truck', slots: [] },
      { id: 'T-2', state: 'loading', slots: ['A1', 'A2'] },
      { id: 'T-3', state: 'waiting', slots: [] },
      { id: 'T-4', state: 'left', slots: ['A3'] },
      { id: 'T-5', state: 'late', slots: ['A5'] },
    ]
    render(<Dock trucks={trucks} />)
    const card = (id: string) => screen.getByText(id).parentElement as HTMLElement
    expect(card('T-1').className).toBe('truck')
    expect(card('T-2')).toHaveClass('truck', 'loading')
    expect(within(card('T-2')).getByText('Loading 2 cars')).toBeInTheDocument()
    expect(card('T-3')).toHaveClass('truck', 'waiting')
    expect(card('T-4')).toHaveClass('truck', 'left')
    expect(within(card('T-4')).getByText('Left · 1 car')).toBeInTheDocument()
    expect(within(card('T-3')).getByText('Here early · waiting')).toBeInTheDocument()
    expect(card('T-5')).toHaveClass('truck', 'late')
    expect(within(card('T-5')).getByText('Running late')).toBeInTheDocument()
  })

  it('CarDetail shows where the car is and tagged decisions, newest first', () => {
    const selected = car('P007', 'parked', {
      slot: 'A3',
      decisions: [
        { at: 10, tag: 'GEMINI', text: 'Gate PASS: blue', exception: false },
        { at: 20, tag: 'YARD', text: 'Assigned A3', exception: false },
        { at: 30, tag: 'SUPERVISOR', text: 'chose wait_for_removal', exception: true },
        { at: 40, tag: 'PLANT', text: 'Parked in A3', exception: false, degraded: true },
      ],
    })
    render(<CarDetail car={selected} />)
    expect(screen.getByRole('heading', { name: /Car P007/ })).toBeInTheDocument()
    expect(screen.getByText('Parked in A3', { selector: 'strong' })).toBeInTheDocument()
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(4)
    const tags = items.map((item) => item.querySelector('.tag') as HTMLElement)
    expect(tags.map((tag) => tag.textContent)).toEqual(['PLANT', 'SUPERVISOR', 'YARD', 'GEMINI'])
    expect(tags[0]).toHaveClass('tag', 'PLANT')
    expect(tags[3]).toHaveClass('tag', 'GEMINI')
    expect(within(items[0]).getByText('06:00:40')).toBeInTheDocument()
    expect(within(items[0]).getByText('(offline)')).toBeInTheDocument()
  })

  it('CarDetail asks to pick a car when nothing is selected', () => {
    render(<CarDetail car={null} />)
    expect(screen.getByText(/click any car/i)).toBeInTheDocument()
  })

  it('WaitingOnYou renders options, marks the recommended one and calls onChoose', () => {
    const onChoose = vi.fn()
    const item: AssistCase = {
      case_id: 'AC-P002-1',
      vin: 'P002',
      reason: 'Box and worker blocking the lane',
      options: ['wait_for_removal', 'pass_left_3kmh'],
      frames: ['ZnJhbWU6MA=='],
      analysis: {
        description: 'A worker is moving the box.',
        boxes: [],
        recommendation: 'wait_for_removal',
        degraded: false,
      },
      status: 'open',
      opened_at: 5,
    }
    render(<WaitingOnYou cases={[item]} onChoose={onChoose} />)
    const img = screen.getByRole('img', { name: /Clip frame from P002/ })
    expect(img).toHaveAttribute('src', 'data:image/jpeg;base64,ZnJhbWU6MA==')
    expect(screen.getByText(/A worker is moving the box/)).toBeInTheDocument()
    const wait = screen.getByRole('button', { name: /Wait for removal/ })
    const pass = screen.getByRole('button', { name: /Pass left at 3 km\/h/ })
    expect(wait).toHaveClass('opt', 'rec')
    expect(wait).toHaveTextContent('recommended')
    expect(pass).toHaveClass('opt')
    expect(pass).not.toHaveClass('rec')

    fireEvent.click(pass)
    expect(onChoose).toHaveBeenCalledWith('AC-P002-1', 'pass_left_3kmh')

    fireEvent.error(img)
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText(/Clip frame from P002/)).toBeInTheDocument()
  })

  it('WaitingOnYou shows the empty state', () => {
    render(<WaitingOnYou cases={[]} onChoose={vi.fn()} />)
    expect(screen.getByText(/nothing needs you/i)).toBeInTheDocument()
  })

  it('DecisionFeed exceptions toggle filters entries', () => {
    const feed: FeedEntry[] = [
      { vin: 'P001', at: 1, tag: 'GEMINI', text: 'Gate PASS: blue', exception: false },
      { vin: 'P002', at: 2, tag: 'PLANT', text: 'Stopped: box', exception: true },
      { vin: 'P003', at: 3, tag: 'YARD', text: 'Assigned A3', exception: false },
    ]
    render(<DecisionFeed feed={feed} />)
    const list = screen.getByRole('list', { name: /decisions/i })
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent('P003: Assigned A3')
    const everything = screen.getByRole('button', { name: 'Everything' })
    const exceptions = screen.getByRole('button', { name: 'Exceptions only' })
    expect(everything).toHaveClass('seg', 'on')
    expect(everything).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(exceptions)
    expect(exceptions).toHaveClass('seg', 'on')
    expect(exceptions).toHaveAttribute('aria-pressed', 'true')
    expect(everything).not.toHaveClass('on')
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(1)
    expect(items[0]).toHaveTextContent('P002: Stopped: box')
    expect(items[0].querySelector('.tag')).toHaveClass('tag', 'PLANT')
  })

  it('DecisionFeed tells a rejected input apart from AI unavailable', () => {
    const feed: FeedEntry[] = [
      { vin: 'P001', at: 1, tag: 'GEMINI', text: 'Gate PASS', exception: false, degraded: true },
      {
        vin: 'P002',
        at: 2,
        tag: 'GEMINI',
        text: 'Gate FAIL',
        exception: true,
        degraded: true,
        invalidInput: true,
      },
    ]
    render(<DecisionFeed feed={feed} />)
    const [rejected, offline] = screen.getAllByRole('listitem')
    expect(within(rejected).getByText('(offline: input rejected)')).toHaveClass('md-offline')
    expect(within(offline).getByText('(offline)')).toHaveClass('md-offline')
  })

  it('Header shows the clock and wires the buttons and the pause toggle', () => {
    const handlers = {
      onPlayPause: vi.fn(),
      onNextException: vi.fn(),
      onRestart: vi.fn(),
      onPauseWhenNeeded: vi.fn(),
    }
    render(
      <Header
        simTime={3725}
        playing={false}
        done={false}
        pauseWhenNeeded
        speed={4}
        {...handlers}
      />,
    )
    expect(screen.getByText(/07:02:05/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Play shift' }))
    fireEvent.click(screen.getByRole('button', { name: 'Next exception' }))
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }))
    expect(handlers.onPlayPause).toHaveBeenCalledTimes(1)
    expect(handlers.onNextException).toHaveBeenCalledTimes(1)
    expect(handlers.onRestart).toHaveBeenCalledTimes(1)
    const toggle = screen.getByRole('checkbox', { name: /pause when i'm needed/i })
    expect(toggle).toBeChecked()
    fireEvent.click(toggle)
    expect(handlers.onPauseWhenNeeded).toHaveBeenCalledWith(false)
  })
})

describe('Dashboard with the shift store', () => {
  afterEach(() => {
    cleanup()
    useShiftStore.getState().pause()
  })

  it('shows a car moving from the gate into the yard', async () => {
    useShiftStore.getState().configure({
      ai: createOfflineMarshalAi(),
      scenario: { cars: [{ vin: 'P001', colour: 'blue' }] },
    })
    render(<Dashboard />)
    const gate = () => screen.getByRole('region', { name: 'At the gate' })
    expect(within(gate()).getByRole('button', { name: /P001/ })).toHaveClass('chip', 'gate')
    expect(screen.getByRole('button', { name: 'Slot A1, empty' })).toBeInTheDocument()

    let parked = false
    for (let t = 0; t < 600 && !parked; t += 1) {
      await act(async () => {
        useShiftStore.getState().advance(1)
        await flush()
      })
      parked = useShiftStore.getState().shift.cars.P001?.stage === 'parked'
    }
    expect(parked).toBe(true)
    expect(within(gate()).queryByRole('button')).toBeNull()
    const cell = screen.getByRole('button', { name: /^Slot A1, P001/ })
    expect(cell.className).toMatch(/cell (fresh|parked)/)
    expect(screen.getByText('In the yard').parentElement).toHaveTextContent('1 / 80')

    fireEvent.click(cell)
    expect(screen.getByRole('heading', { name: /Car P001/ })).toBeInTheDocument()
  })

  it('choosing an assist option sends assist.choose chosen by the supervisor', async () => {
    useShiftStore.getState().configure({
      ai: createOfflineMarshalAi(),
      scenario: {
        cars: [{ vin: 'P002', colour: 'white' }],
        blockers: [
          {
            vin: 'P002',
            at: 1,
            reason: 'Box and worker blocking the lane',
            options: ['wait_for_removal', 'pass_left_3kmh'],
            withWorker: true,
          },
        ],
      },
    })
    render(<Dashboard />)
    await act(async () => {
      useShiftStore.getState().advance(2)
      await flush()
    })
    expect(useShiftStore.getState().shift.paused).toBe(true)
    const card = screen.getByRole('article', { name: 'Assistance · P002' })
    const wait = within(card).getByRole('button', { name: /Wait for removal/ })
    expect(wait).toHaveClass('opt', 'rec')

    await act(async () => {
      fireEvent.click(wait)
      await flush()
    })
    const backend = getShiftBackend()
    expect(backend).toBeInstanceOf(ScriptedDrivingSystem)
    const chosen = (backend as ScriptedDrivingSystem).commands.filter(
      (cmd): cmd is AssistChooseCmd => cmd.op === 'assist.choose',
    )
    expect(chosen).toEqual([
      {
        op: 'assist.choose',
        case_id: 'AC-P002-1',
        vin: 'P002',
        option: 'wait_for_removal',
        chosen_by: 'supervisor',
      },
    ])
    expect(screen.getByText(/nothing needs you/i)).toBeInTheDocument()
    expect(useShiftStore.getState().shift.paused).toBe(false)
  })

  it('plays the default shift to the end, including Scenes A, D and E', async () => {
    useShiftStore.getState().configure({ ai: createOfflineMarshalAi(), scenario: DEFAULT_SCENARIO })
    useShiftStore.getState().setPauseWhenNeeded(false)
    const store = () => useShiftStore.getState()
    expect(store().shift.kpis.offLine).toBe(5)
    expect(store().shift.progress).toBe(0)
    for (let t = 0; t < 2400 && store().shift.progress < 1; t += 2) {
      await act(async () => {
        store().advance(2)
        await flush()
      })
    }
    const { shift } = store()
    expect(shift.progress).toBe(1)
    const texts = shift.feed.map((entry) => entry.text)
    expect(texts.some((text) => text.startsWith('Route via lane_c'))).toBe(true)
    // Scene D: the late truck is shown as late (not "here early") and P090 moves up.
    expect(texts.some((text) => text.startsWith('Re-plan A5 -> '))).toBe(true)
    expect(shift.trucks.find((truck) => truck.id === 'T-1')?.state).toBe('late')
    expect(shift.layout.A5).not.toBe('P090')
    // Scene E: P001 parks in A7 (yard.Y7) right behind P091 in A8 (yard.Y8).
    expect(shift.cars.P001).toMatchObject({ stage: 'parked', slot: 'A7' })
    expect(shift.layout).toMatchObject({ A7: 'P001', A8: 'P091' })
    expect(texts).toContain('Parked in A7 (Y7)')
    expect(shift.hazard).toBeNull()
    store().setPauseWhenNeeded(true)
  })

  it('keeps image checks offline while the engine only has placeholder frames', async () => {
    const ai = defaultShiftAi({ placeholderFrames: true })
    const frames = { front: 'x', rear: 'x', left: 'x', right: 'x' }
    const hints = { front: 'colour=red' }
    const gate = await ai.evaluateGatePhotos({ vin: 'V', expectedColour: 'red', frames, hints })
    expect(gate).toMatchObject({ pass: true, degraded: true })
    const lamp = await ai.evaluateLampEvidence({
      vin: 'V',
      step: 'headlights',
      telemetry: { headlights: 'ON' },
      frames: [],
      hint: 'headlights=off',
    })
    expect(lamp).toMatchObject({ pass: false, observed: 'OFF', degraded: true })
    const assist = await ai.analyzeAssistClip({
      case_id: 'c',
      reason: 'Box in the lane',
      options: ['wait_for_removal', 'pass_left_3kmh'],
      frames: [],
    })
    expect(assist).toMatchObject({ recommendation: 'pass_left_3kmh', degraded: true })
    expect(new ScriptedDrivingSystem().placeholderFrames).toBe(true)
  })
})
