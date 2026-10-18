import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { Dashboard } from '#/components/Dashboard'
import { SimCanvas } from '#/components/sim/SimCanvas'
import { FONTS_STYLESHEET } from '#/lib/designTokens'
import { createLiveBackend } from '#/lib/simulator/liveBackend'
import { useShiftStore } from '#/stores/shift'

export const Route = createFileRoute('/')({
  ssr: false,
  head: () => ({
    meta: [{ title: 'Marshal · Live shift' }],
    links: [
      { rel: 'preconnect', href: 'https://fonts.googleapis.com' },
      { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossOrigin: 'anonymous' },
      { rel: 'stylesheet', href: FONTS_STYLESHEET },
    ],
  }),
  component: ShiftPage,
})

/**
 * True unless the URL asks for the scripted engine (`?engine=scripted`).
 * @returns Whether the page runs the three.js engine with live cameras.
 */
function wantsLiveEngine(): boolean {
  if (typeof window === 'undefined') return false
  return new URLSearchParams(window.location.search).get('engine') !== 'scripted'
}

function ShiftPage() {
  // Swap the backend during the first render, before the 3D view and the store start.
  const [live] = useState(() => {
    const on = wantsLiveEngine()
    if (on) useShiftStore.getState().setBackend(createLiveBackend)
    return on
  })
  return <Dashboard simCanvas={<SimCanvas dressed={live} />} />
}
