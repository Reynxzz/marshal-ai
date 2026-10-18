# syntax=docker/dockerfile:1
# Production image for Cloud Run: build with Bun, serve dist/ with serve.mjs on port 8080.

ARG BUN_VERSION=1.4.3

# 1) All dependencies (dev included) for the build.
FROM oven/bun:${BUN_VERSION} AS deps
WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

# 2) Build dist/client + dist/server with vite (TanStack Start plugin).
# VITE_BACKEND_API_URL is inlined into the browser bundle: the Marshal API base URL. The load
# balancer routes /api/* to the API, so the image calls it same-origin; local dev (bun run dev,
# no variable) keeps the default in src/lib/serverFns/client.ts.
FROM deps AS build
ARG VITE_BACKEND_API_URL=/api/v1
COPY . .
ENV NODE_ENV=production \
    VITE_BACKEND_API_URL=${VITE_BACKEND_API_URL}
RUN bun run build

# 3) Production-only dependencies for runtime imports from dist/server.
FROM oven/bun:${BUN_VERSION} AS prod-deps
WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile --production

# 4) Runtime.
FROM oven/bun:${BUN_VERSION} AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    PORT=8080
COPY --from=prod-deps --chown=bun:bun /app/node_modules ./node_modules
COPY --from=build --chown=bun:bun /app/dist ./dist
COPY --chown=bun:bun package.json serve.mjs ./
USER bun
EXPOSE 8080
CMD ["bun", "run", "serve.mjs"]
