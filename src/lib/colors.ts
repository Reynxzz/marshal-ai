// Body colour palette shared by the renderer, the truth capture and the manifest.
// Build-sheet colour names are the source of truth; hex is what the 3D body uses.

export const BUILD_COLOURS = {
  blue: '#2f6fed',
  white: '#f2f2f2',
  grey: '#8a8f98',
  black: '#1c1c1c',
} as const

export type ColourName = keyof typeof BUILD_COLOURS

/** Spawn colours for `wrong_colour` cars: declared blue/white/grey/black, spawned off-sheet. */
export const WRONG_COLOURS = {
  red: '#e23b2e',
  orange: '#e8862a',
  green: '#3aa15f',
} as const

export type WrongColourName = keyof typeof WRONG_COLOURS

/** Default wrong spawn colour (red); the case library can pick any of WRONG_COLOURS. */
export const WRONG_COLOUR = { name: 'red' as const, hex: WRONG_COLOURS.red }

export function colourHex(name: string): string {
  if (name in BUILD_COLOURS) return BUILD_COLOURS[name as ColourName]
  if (name in WRONG_COLOURS) return WRONG_COLOURS[name as WrongColourName]
  return BUILD_COLOURS.grey
}

export function colourName(hex: string): string {
  const normalized = hex.toLowerCase()
  for (const [name, value] of Object.entries(BUILD_COLOURS)) {
    if (value.toLowerCase() === normalized) return name
  }
  for (const [name, value] of Object.entries(WRONG_COLOURS)) {
    if (value.toLowerCase() === normalized) return name
  }
  return 'unknown'
}
