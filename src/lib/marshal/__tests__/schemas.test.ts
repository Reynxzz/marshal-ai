import { describe, expect, it } from 'vitest'
import type { z } from 'zod'
import {
  assistInputSchema,
  gateInputSchema,
  lampInputSchema,
  MAX_FRAME_CHARS,
  MAX_FRAMES,
  MAX_OBSTACLES,
  MAX_SLOTS,
  MAX_TELEMETRY_KEYS,
  MAX_TEXT_CHARS,
  parkingInputSchema,
  routeInputSchema,
  yardInputSchema,
} from '../schemas'

const frames = { front: 'AAAA', rear: 'AAAA', left: 'AAAA', right: 'AAAA' }
const pose = { x: 0, y: 0, yaw: 0 }
const slot = { id: 'yard.Y1', x: 0, y: 0, yaw: 90 }

const ok = (schema: z.ZodType, value: unknown): boolean => schema.safeParse(value).success

describe('server function input schemas', () => {
  it('accept well-formed inputs', () => {
    expect(ok(gateInputSchema, { vin: 'V', expectedColour: 'red', frames })).toBe(true)
    const lamp = { vin: 'V', step: 'headlights', telemetry: {}, frames: [] }
    expect(ok(lampInputSchema, lamp)).toBe(true)
    const assist = { case_id: 'c1', reason: 'box', options: ['pass_left_3kmh'], frames: [] }
    expect(ok(assistInputSchema, assist)).toBe(true)
    expect(ok(parkingInputSchema, { vin: 'V', pose, obstacles: [], slot })).toBe(true)
    expect(ok(routeInputSchema, { hazardDescription: '', options: ['lane_c'] })).toBe(true)
    const layout = { 'yard.Y1': null, 'yard.Y2': 'V' }
    expect(ok(yardInputSchema, { layout, constraints: {} })).toBe(true)
  })

  it('reject malformed inputs', () => {
    // Views dropped client-side (too large) may be missing, but at least one must be sent.
    const threeViews = { front: 'AAAA', rear: 'AAAA', left: 'AAAA' }
    expect(ok(gateInputSchema, { vin: 'V', expectedColour: 'red', frames: threeViews })).toBe(true)
    expect(ok(gateInputSchema, { vin: 'V', expectedColour: 'red', frames: {} })).toBe(false)
    expect(ok(gateInputSchema, { vin: 'V', expectedColour: '', frames })).toBe(false)
    const lamp = { vin: 'V', step: '', telemetry: {}, frames: [] }
    expect(ok(lampInputSchema, lamp)).toBe(false)
    const noOptions = { case_id: 'c', reason: 'r', options: [], frames: [] }
    expect(ok(assistInputSchema, noOptions)).toBe(false)
    const badOption = { case_id: 'c', reason: 'r', options: ['drive_through'], frames: [] }
    expect(ok(assistInputSchema, badOption)).toBe(false)
    const noYaw = { vin: 'V', pose: { x: 0, y: 0 }, obstacles: [], slot }
    expect(ok(parkingInputSchema, noYaw)).toBe(false)
    const flat = { id: 'o', x: 0, y: 0, yaw: 0, length: 0, width: 1, moving: false }
    expect(ok(parkingInputSchema, { vin: 'V', pose, obstacles: [flat], slot })).toBe(false)
    expect(ok(routeInputSchema, { hazardDescription: 'x', options: ['detour'] })).toBe(false)
    expect(ok(yardInputSchema, { layout: { 'yard.Y1': 3 }, constraints: {} })).toBe(false)
    expect(ok(yardInputSchema, { layout: {} })).toBe(false)
  })

  it('bound list and string sizes (public endpoints)', () => {
    const lamp = { vin: 'V', step: 'headlights', telemetry: {}, frames: [] as string[] }
    const many = Array.from({ length: MAX_FRAMES + 1 }, () => 'AAAA')
    expect(ok(lampInputSchema, { ...lamp, frames: many })).toBe(false)
    const huge = 'A'.repeat(MAX_FRAME_CHARS + 1)
    const bigGate = { vin: 'V', expectedColour: 'red', frames: { ...frames, front: huge } }
    expect(ok(gateInputSchema, bigGate)).toBe(false)
    const box = { id: 'o', x: 0, y: 0, yaw: 0, length: 1, width: 1, moving: false }
    const park = (n: number) => ({
      vin: 'V',
      pose,
      slot,
      obstacles: Array.from({ length: n }, (_, i) => ({ ...box, id: `o${i}` })),
    })
    expect(ok(parkingInputSchema, park(MAX_OBSTACLES))).toBe(true)
    expect(ok(parkingInputSchema, park(MAX_OBSTACLES + 1))).toBe(false)
    const reason = 'x'.repeat(MAX_TEXT_CHARS + 1)
    expect(ok(routeInputSchema, { hazardDescription: reason, options: ['lane_c'] })).toBe(false)
    const keys = (n: number) => Array.from({ length: n }, (_, i) => `k${i}`)
    const layout = Object.fromEntries(keys(MAX_SLOTS + 1).map((key) => [key, null]))
    expect(ok(yardInputSchema, { layout, constraints: {} })).toBe(false)
    const telemetry = Object.fromEntries(keys(MAX_TELEMETRY_KEYS + 1).map((key) => [key, 1]))
    expect(ok(lampInputSchema, { ...lamp, telemetry })).toBe(false)
  })
})
