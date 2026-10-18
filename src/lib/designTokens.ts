// Marshal dashboard design tokens (TEAM_PLAN M8, taken from Main.dc.html). The dashboard
// exposes them as CSS custom properties so src/components/dashboard.css uses them.

/** Dashboard colours. */
export const colors = {
  bg: '#12171D',
  surface: '#1B222B',
  surfaceAlt: '#151B22',
  border: '#2A3340',
  borderAlt: '#344052',
  text: '#E9E6DE',
  textMuted: '#A9B1BC',
  textDim: '#8C95A1',
  accent: '#E39B2E', // amber — alert / CTA
  blue: '#7FA7FF', // gate chips, links
  blueLight: '#B8CCFF', // lane chips, just-parked cells
  sand: '#D9D5CB', // parked cells
  gold: '#F6C77A', // KPI highlight
  rework: '#C2621F', // rework chips
}

/** Dashboard font stacks. */
export const fonts = {
  ui: "'IBM Plex Sans', sans-serif",
  mono: "'IBM Plex Mono', monospace",
  head: "'Space Grotesk', sans-serif",
}

/** Google Fonts stylesheet used by Main.dc.html (IBM Plex Sans/Mono, Space Grotesk). */
export const FONTS_STYLESHEET =
  'https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap'

/**
 * The tokens as CSS custom properties (`--md-<name>`), applied on the dashboard root.
 * @returns Property name -> value.
 */
export function tokenCssVars(): Record<string, string> {
  const vars: Record<string, string> = {}
  for (const [name, value] of Object.entries(colors)) vars[`--md-${kebab(name)}`] = value
  for (const [name, value] of Object.entries(fonts)) vars[`--md-font-${name}`] = value
  return vars
}

function kebab(name: string): string {
  return name.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)
}
