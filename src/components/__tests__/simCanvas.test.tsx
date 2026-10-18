import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { bus } from '#/lib/marshal/eventBus'
import { createOfflineMarshalAi } from '#/lib/marshal/testing/offlineAi'
import type { SimSnapshot } from '#/lib/simview/snapshot'
import type { SimViewerFactory } from '#/lib/simview/viewer'
import { shiftFocus, useShiftStore } from '#/stores/shift'
import { Dashboard } from '../Dashboard'
import { SimCanvas } from '../sim/SimCanvas'

async function flush(): Promise<void> {
  for (let i = 0; i < 50; i++) await Promise.resolve()
}

function fakeViewer() {
  const frames: SimSnapshot[] = []
  const dispose = vi.fn()
  const factory: SimViewerFactory = () => ({
    frame: (snapshot) => {
      frames.push(snapshot)
    },
    resize: () => {},
    dispose,
  })
  return { factory, frames, dispose }
}

const caption = () => screen.getByText(/^(Focused|Overview):/)

describe('SimCanvas on the shift dashboard', () => {
  afterEach(() => {
    cleanup()
    useShiftStore.getState().pause()
  })

  it('falls back to a text message when WebGL fails and still captions the focus', async () => {
    useShiftStore.getState().configure({
      ai: createOfflineMarshalAi(),
      scenario: { cars: [{ vin: 'P001', colour: 'blue' }] },
    })
    const broken: SimViewerFactory = () => {
      throw new Error('no WebGL')
    }
    render(<Dashboard simCanvas={<SimCanvas createViewer={broken} />} />)
    await act(flush)
    expect(screen.getByRole('status')).toHaveTextContent(/3D view unavailable/)
    expect(screen.queryByRole('img', { name: /3D plant view/ })).toBeNull()
    expect(caption()).toHaveTextContent('Overview: 1 car on the plant')
  })

  it('focuses the car clicked on the lanes and returns to the overview', async () => {
    useShiftStore.getState().configure({
      ai: createOfflineMarshalAi(),
      scenario: { cars: [{ vin: 'P001', colour: 'blue' }] },
    })
    const viewer = fakeViewer()
    const view = render(<Dashboard simCanvas={<SimCanvas createViewer={viewer.factory} />} />)
    const canvas = screen.getByRole('img', { name: /3D plant view/ })
    expect(canvas).toHaveAccessibleName('3D plant view. Overview: 1 car on the plant.')

    const gate = screen.getByRole('region', { name: 'At the gate' })
    fireEvent.click(within(gate).getByRole('button', { name: /P001/ }))
    expect(useShiftStore.getState().selected).toBe('P001')
    expect(shiftFocus(useShiftStore.getState())).toMatchObject({ vin: 'P001', reason: 'selected' })
    expect(caption()).toHaveTextContent('Focused: P001 at line_end — selected')
    expect(canvas).toHaveAccessibleName(/following car P001 at line_end, selected/)

    // The render loop reads the engine and passes the focus pose to the viewer.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60))
    })
    const last = viewer.frames.at(-1)
    expect(last?.focus?.vin).toBe('P001')
    expect(last?.focusPose).toMatchObject({ x: 8, y: 5.25 })
    expect(last?.cars.find((item) => item.vin === 'P001')?.highlight).toBe('selected')

    fireEvent.click(screen.getByRole('button', { name: 'Show the plant overview' }))
    expect(useShiftStore.getState().selected).toBeNull()
    expect(caption()).toHaveTextContent('Overview: 1 car on the plant')
    view.unmount()
    expect(viewer.dispose).toHaveBeenCalledTimes(1)
  })

  it('auto-focuses an open assist case and "Show in 3D" picks a case', async () => {
    useShiftStore.getState().configure({
      ai: createOfflineMarshalAi(),
      scenario: {
        cars: [
          { vin: 'P001', colour: 'blue' },
          { vin: 'P002', colour: 'white' },
        ],
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
    const viewer = fakeViewer()
    render(<Dashboard simCanvas={<SimCanvas createViewer={viewer.factory} />} />)
    await act(async () => {
      useShiftStore.getState().advance(2)
      await flush()
    })
    expect(caption()).toHaveTextContent(/^Focused: P002 at .+ — waiting on you$/)

    fireEvent.click(
      screen.getByRole('region', { name: 'At the gate' }).querySelector('button') as HTMLElement,
    )
    expect(caption()).toHaveTextContent(/^Focused: P001 at .+ — selected$/)

    const card = screen.getByRole('article', { name: 'Assistance · P002' })
    const show = within(card).getByRole('button', { name: 'Show P002 in 3D' })
    expect(show).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(show)
    expect(useShiftStore.getState()).toMatchObject({ selected: 'P002', focusedCase: 'AC-P002-1' })
    expect(show).toHaveAttribute('aria-pressed', 'true')
    expect(caption()).toHaveTextContent(/— waiting on you$/)

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60))
    })
    const last = viewer.frames.at(-1)
    expect(last?.cars.find((item) => item.vin === 'P002')?.highlight).toBe('needs')
    // The box and the worker spawned in front of P002 are drawn as blocking it.
    expect(
      last?.props
        .filter((prop) => prop.blocking)
        .map((prop) => prop.kind)
        .sort(),
    ).toEqual(['box', 'worker'])

    // "Overview" dismisses the open case until a new decision comes up.
    fireEvent.click(screen.getByRole('button', { name: 'Show the plant overview' }))
    expect(caption()).toHaveTextContent('Overview: 2 cars on the plant')
  })

  it('shows the latest orchestrator note as a pill (M8.10)', async () => {
    useShiftStore.getState().configure({
      ai: createOfflineMarshalAi(),
      scenario: { cars: [{ vin: 'P001', colour: 'blue' }] },
    })
    const viewer = fakeViewer()
    render(<Dashboard simCanvas={<SimCanvas createViewer={viewer.factory} />} />)
    await act(flush)

    act(() => {
      bus.emit('ui:note', { text: 'VIN-P1 parked in Y7', kind: 'plant' })
    })
    expect(screen.getByText(/VIN-P1 parked in Y7/)).toHaveTextContent('PLANT · VIN-P1 parked in Y7')

    act(() => {
      bus.emit('ui:note', { text: 'P001 parking Y7: path B (0.81)', kind: 'gemini' })
    })
    const pill = screen.getByText(/path B/)
    expect(pill).toHaveClass('gemini')
    expect(pill).toHaveTextContent('GEMINI · P001 parking Y7: path B (0.81)')
  })
})
