import { createRootRoute, HeadContent, Scripts } from '@tanstack/react-router'

import appCss from '../styles.css?url'

export const Route = createRootRoute({
  head: () => ({
    meta: [
      {
        charSet: 'utf-8',
      },
      {
        name: 'viewport',
        content: 'width=device-width, initial-scale=1',
      },
      {
        title: 'Marshal Three.js — Plant Simulation',
      },
      {
        name: 'description',
        content:
          'Browser-native car plant simulation: run a batch through seven flows with offline AI feedback.',
      },
      {
        name: 'theme-color',
        content: '#0a0c11',
      },
    ],
    links: [
      {
        rel: 'stylesheet',
        href: appCss,
      },
      {
        rel: 'icon',
        href: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"%3E%3Crect width="32" height="32" rx="8" fill="%23121620"/%3E%3Cpath d="M8 23 12 9h8l4 14" fill="none" stroke="%23dce6ff" stroke-width="2.4" stroke-linejoin="round"/%3E%3Cpath d="M11 18h10" stroke="%234f8cff" stroke-width="2.4" stroke-linecap="round"/%3E%3C/svg%3E',
      },
    ],
  }),
  shellComponent: RootDocument,
  notFoundComponent: RootNotFound,
})

function RootNotFound() {
  return (
    <div className="card max-w-md mx-auto my-12 p-8 text-center rounded-2xl bg-slate-900/80 border border-white/10 shadow-2xl">
      <h2 className="text-xl font-bold text-white mb-2">404 — Page Not Found</h2>
      <p className="text-sm text-slate-400">The requested route could not be found.</p>
    </div>
  )
}

function RootDocument({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark min-h-full">
      <head>
        <HeadContent />
      </head>
      <body className="bg-[#07090e] text-slate-100 min-h-screen font-sans antialiased selection:bg-sky-500/30 selection:text-white">
        {children}

        <Scripts />
      </body>
    </html>
  )
}
