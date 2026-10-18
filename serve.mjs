// Production server for Cloud Run (run with Bun, no extra dependencies).
//
// `vite build` (TanStack Start vite plugin) emits:
//   dist/client/            static browser assets (hashed files under /assets)
//   dist/server/server.js   default export `{ fetch(request) }` handling SSR + server functions
//
// This server serves dist/client as static files and forwards every other request to that
// handler. GET /healthz answers 200 for the Cloud Run startup probe and the load balancer.
import { stat } from 'node:fs/promises'
import { resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const PORT = Number(process.env.PORT ?? 8080)
const CLIENT_DIR = fileURLToPath(new URL('./dist/client', import.meta.url))
const SERVER_ENTRY = new URL('./dist/server/server.js', import.meta.url).href

const { default: app } = await import(SERVER_ENTRY)
if (!app || typeof app.fetch !== 'function') {
  console.error(`[serve] ${SERVER_ENTRY} does not default-export an object with fetch()`)
  process.exit(1)
}

/**
 * Resolve a URL pathname to a file inside CLIENT_DIR, refusing path traversal.
 * @param {string} pathname URL pathname (still percent-encoded)
 * @returns {Promise<string | null>} absolute file path, or null when no static file matches
 */
async function findStaticFile(pathname) {
  let decoded
  try {
    decoded = decodeURIComponent(pathname)
  } catch {
    return null
  }
  if (decoded.includes('\0')) return null
  const filePath = resolve(CLIENT_DIR, `.${decoded}`)
  if (!filePath.startsWith(CLIENT_DIR + sep)) return null
  try {
    const info = await stat(filePath)
    return info.isFile() ? filePath : null
  } catch {
    return null
  }
}

/**
 * Handle one HTTP request: health check, static asset, or TanStack Start handler.
 * @param {Request} request incoming request
 * @returns {Promise<Response>} response
 */
async function handle(request) {
  const url = new URL(request.url)

  if (url.pathname === '/healthz') {
    return new Response('ok', { headers: { 'Content-Type': 'text/plain' } })
  }

  if (request.method === 'GET' || request.method === 'HEAD') {
    const filePath = await findStaticFile(url.pathname)
    if (filePath) {
      const file = Bun.file(filePath)
      return new Response(request.method === 'HEAD' ? null : file, {
        headers: {
          'Content-Type': file.type || 'application/octet-stream',
          'Content-Length': String(file.size),
          'Cache-Control': url.pathname.startsWith('/assets/')
            ? 'public, max-age=31536000, immutable'
            : 'public, max-age=3600',
        },
      })
    }
  }

  return app.fetch(request)
}

const server = Bun.serve({
  port: PORT,
  hostname: '0.0.0.0',
  fetch: handle,
  error(error) {
    console.error('[serve] unhandled error', error)
    return new Response('Internal Server Error', { status: 500 })
  },
})

console.log(`[serve] listening on 0.0.0.0:${server.port}`)

// Cloud Run sends SIGTERM before stopping an instance: finish in-flight requests, then exit.
process.on('SIGTERM', async () => {
  await server.stop()
  process.exit(0)
})
