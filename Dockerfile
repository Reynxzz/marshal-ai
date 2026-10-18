# syntax=docker/dockerfile:1
# Production image for Cloud Run: the Marshal Control Tower API (marshal_api) on uvicorn, port $PORT.
# Settings come from the environment (MARSHAL_*, GOOGLE_*), set on the Cloud Run service.

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.11.8

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# 1) Locked dependencies (+ the gcp extra: Firestore, Pub/Sub, GCS, BigQuery), then the project.
FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra gcp --no-install-project
COPY README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY scripts ./scripts
RUN uv sync --frozen --no-dev --extra gcp --no-editable

# 2) Runtime: no uv, non-root user, writable data dir for the SQLite demo database.
FROM python:${PYTHON_VERSION}-slim AS runtime
RUN useradd --create-home --uid 10001 marshal
WORKDIR /app
COPY --from=build --chown=marshal:marshal /app /app
RUN mkdir -p /app/data && chown marshal:marshal /app/data
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    MARSHAL_DB_URL=sqlite+aiosqlite:////app/data/marshal.db
USER marshal
EXPOSE 8080
CMD ["sh", "scripts/start_api.sh"]
