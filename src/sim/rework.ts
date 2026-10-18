// Rework bay routing (flow 5). Bays are visited in a fixed order.

export const REWORK_BAYS = [
  'rework.paint',
  'rework.body',
  'rework.electrical',
  'rework.mechanical',
] as const

/** Pick and order the bays needed to fix the given findings. */
export function planRework(findings: string[]): string[] {
  const needed = new Set<string>()
  for (const f of findings) {
    if (/colour|paint|color/i.test(f)) needed.add('rework.paint')
    else if (/lamp|light|electrical|headlight|brake/i.test(f)) needed.add('rework.electrical')
    else needed.add('rework.body')
  }
  if (needed.size === 0) needed.add('rework.body')
  return REWORK_BAYS.filter((b) => needed.has(b))
}

/** The next bay after `current`, or null when rework is complete. */
export function nextBay(current: string): string | null {
  const i = REWORK_BAYS.indexOf(current as (typeof REWORK_BAYS)[number])
  if (i === -1 || i === REWORK_BAYS.length - 1) return null
  return REWORK_BAYS[i + 1] as string
}
