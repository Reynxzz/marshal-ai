import { describe, expect, it, vi } from 'vitest'
import { bus, createEventBus, type MarshalEventMap } from '../eventBus'
import type { StatusEvent } from '../types'

const status: StatusEvent = {
  event: 'status',
  vin: 'VIN-1',
  location: 'line_end',
  x: 8,
  y: 5.25,
  speed_kmh: 0,
  mission_id: 'm1',
  progress: 0,
  held: false,
  stopped_for_assist: false,
}

describe('createEventBus', () => {
  it('delivers payloads to subscribers of the same event only', () => {
    const events = createEventBus<MarshalEventMap>()
    const onStatus = vi.fn()
    const onNote = vi.fn()
    events.on('sim:status', onStatus)
    events.on('ui:note', onNote)
    events.emit('sim:status', status)
    expect(onStatus).toHaveBeenCalledWith(status)
    expect(onNote).not.toHaveBeenCalled()
  })

  it('stops delivery after the returned unsubscribe or off', () => {
    const events = createEventBus<MarshalEventMap>()
    const a = vi.fn()
    const b = vi.fn()
    const unsubscribe = events.on('ui:note', a)
    events.on('ui:note', b)
    unsubscribe()
    events.off('ui:note', b)
    events.emit('ui:note', { text: 'hello', kind: 'plant' })
    expect(a).not.toHaveBeenCalled()
    expect(b).not.toHaveBeenCalled()
  })

  it('keeps delivering when a listener throws and tolerates unsubscribe during emit', () => {
    const events = createEventBus<MarshalEventMap>()
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const later = vi.fn()
    const unsubscribe = events.on('marshal:command', () => {
      unsubscribe()
      throw new Error('boom')
    })
    events.on('marshal:command', later)
    const cmd = { op: 'mission.resume', vin: 'VIN-1' } as const
    events.emit('marshal:command', cmd)
    events.emit('marshal:command', cmd)
    expect(later).toHaveBeenCalledTimes(2)
    expect(error).toHaveBeenCalledTimes(1)
    error.mockRestore()
  })

  it('clears one event or every event', () => {
    const events = createEventBus<MarshalEventMap>()
    const a = vi.fn()
    const b = vi.fn()
    events.on('ui:note', a)
    events.on('sim:status', b)
    events.clear('ui:note')
    events.emit('ui:note', { text: 'x', kind: 'yard' })
    events.emit('sim:status', status)
    expect(a).not.toHaveBeenCalled()
    expect(b).toHaveBeenCalledTimes(1)
    events.clear()
    events.emit('sim:status', status)
    expect(b).toHaveBeenCalledTimes(1)
  })

  it('exposes a shared typed default bus', () => {
    const note = vi.fn()
    const unsubscribe = bus.on('ui:note', note)
    bus.emit('ui:note', { text: 'GEMINI note', kind: 'gemini' })
    unsubscribe()
    expect(note).toHaveBeenCalledWith({ text: 'GEMINI note', kind: 'gemini' })
    // @ts-expect-error payload must match the event map
    bus.emit('ui:note', { text: 'x', kind: 'nope' })
  })
})
