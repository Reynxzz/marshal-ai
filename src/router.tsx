import { createRouter as createTanStackRouter } from '@tanstack/react-router'
import { routeTree } from './routeTree.gen'

export function getRouter() {
  const router = createTanStackRouter({
    routeTree,
    scrollRestoration: true,
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 0,
    defaultNotFoundComponent: () => (
      <div style={{ padding: '2rem', textAlign: 'center' }}>
        <h2>404 — Page Not Found</h2>
        <p style={{ color: '#8890a6', marginTop: '0.5rem' }}>
          The requested route could not be found.
        </p>
      </div>
    ),
  })

  return router
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof getRouter>
  }
}
