// In-process typed event emitter (TEAM_PLAN "Event Bus Pattern"). Dependency-free: a listener
// throwing never stops the others, and unsubscribing during emit is safe.

import type {
  AssistRequestEvent,
  MissionDoneEvent,
  SimCommand,
  StatusEvent,
  TrackStepEvent,
} from './types'

/** Dashboard note, e.g. "GEMINI · VIN-P1 parking Y8: path B (0.81)". */
export type MarshalNote = { text: string; kind: 'gemini' | 'plant' | 'yard' }

/** Event name -> payload map for the Marshal bus. */
export type MarshalEventMap = {
  'sim:status': StatusEvent
  'sim:mission.done': MissionDoneEvent
  'sim:track.step': TrackStepEvent
  'sim:assist.request': AssistRequestEvent
  'marshal:command': SimCommand
  'ui:note': MarshalNote
}

/** Any event name -> payload map. */
export type EventMap = Record<string, unknown>

/** A typed emitter over an event map `M`. */
export type EventBus<M extends EventMap> = {
  /**
   * Subscribes to an event.
   * @param name - Event name.
   * @param cb - Listener.
   * @returns Unsubscribe function.
   */
  on<K extends keyof M>(name: K, cb: (payload: M[K]) => void): () => void
  /**
   * Removes a listener.
   * @param name - Event name.
   * @param cb - Listener previously passed to `on`.
   */
  off<K extends keyof M>(name: K, cb: (payload: M[K]) => void): void
  /**
   * Delivers a payload to every current listener of `name`.
   * @param name - Event name.
   * @param payload - Event payload.
   */
  emit<K extends keyof M>(name: K, payload: M[K]): void
  /**
   * Removes listeners.
   * @param name - Event to clear; clears every event when omitted.
   */
  clear(name?: keyof M): void
}

type AnyListener = (payload: unknown) => void

/**
 * Creates an independent typed event bus.
 * @returns A new bus with no listeners.
 */
export function createEventBus<M extends EventMap = MarshalEventMap>(): EventBus<M> {
  const listeners = new Map<keyof M, Set<AnyListener>>()

  const off = <K extends keyof M>(name: K, cb: (payload: M[K]) => void): void => {
    const set = listeners.get(name)
    if (!set) return
    set.delete(cb as AnyListener)
    if (set.size === 0) listeners.delete(name)
  }

  return {
    on(name, cb) {
      let set = listeners.get(name)
      if (!set) {
        set = new Set()
        listeners.set(name, set)
      }
      set.add(cb as AnyListener)
      return () => off(name, cb)
    },
    off,
    emit(name, payload) {
      const set = listeners.get(name)
      if (!set) return
      for (const cb of [...set]) {
        try {
          cb(payload)
        } catch (error) {
          console.error(`[eventBus] listener for ${String(name)} threw:`, error)
        }
      }
    },
    clear(name) {
      if (name === undefined) listeners.clear()
      else listeners.delete(name)
    },
  }
}

/** The shared Marshal bus instance. */
export const bus: EventBus<MarshalEventMap> = createEventBus<MarshalEventMap>()
