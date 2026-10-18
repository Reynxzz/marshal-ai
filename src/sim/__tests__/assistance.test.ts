import { describe, expect, it } from 'vitest'
import { AssistanceDesk } from '../flows/assistance'
import { makeHost } from './helpers'

describe('AssistanceDesk', () => {
  it('never recommends wait_for_removal when nobody is present', async () => {
    const desk = new AssistanceDesk(makeHost())
    const c = await desk.assess('V1', 'a box blocks the lane')
    expect(c.recommended).not.toBe('wait_for_removal')
    expect(c.peopleOrEquipment).toBe(false)
  })

  it('recommends wait_for_removal when a person is present', async () => {
    const desk = new AssistanceDesk(makeHost())
    const c = await desk.assess('V2', 'a worker stands next to a box')
    expect(c.peopleOrEquipment).toBe(true)
    expect(c.options).toContain('wait_for_removal')
  })

  it('escalates after 60s when auto-decide is disabled', async () => {
    const host = makeHost()
    const desk = new AssistanceDesk(host, () => true, Number.POSITIVE_INFINITY)
    const c = await desk.assess('V3', 'a worker blocks the lane')
    expect(c.status).toBe('open')
    desk.tick(c.openedAt + 61)
    expect(c.status).toBe('escalated')
  })

  it('auto-decides headless after the window', async () => {
    const host = makeHost()
    const desk = new AssistanceDesk(host)
    const c = await desk.assess('V4', 'a worker blocks the lane')
    desk.tick(c.openedAt + 91)
    expect(c.status).toBe('resolved')
    expect(c.decidedBy).toBe('auto')
    expect(desk.drainDecisions()).toHaveLength(1)
  })
})
