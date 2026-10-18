#!/bin/sh
# Container entrypoint (Dockerfile): prepare the SQLite demo database, then serve the API.
#
# A SQLite MARSHAL_DB_URL whose file does not exist yet (every new Cloud Run instance) is created
# with the Alembic migrations and brought to the same "from scratch" state as the committed
# marshal.db: base infrastructure only (seed_database.py, then clear_seeded_data.py). A PostgreSQL
# URL is only migrated. Runs from the repository root (alembic.ini, scripts/).
set -eu

DB_URL="${MARSHAL_DB_URL:-sqlite+aiosqlite:///./marshal.db}"

case "$DB_URL" in
  sqlite*)
    DB_FILE="${DB_URL#*:///}"
    if [ ! -f "$DB_FILE" ]; then
      echo "start_api: creating SQLite database $DB_FILE"
      python -m alembic upgrade head
      python scripts/seed_database.py
      python scripts/clear_seeded_data.py
    fi
    ;;
  *)
    python -m alembic upgrade head
    ;;
esac

exec uvicorn marshal_api.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" \
  --proxy-headers \
  --forwarded-allow-ips '*'
