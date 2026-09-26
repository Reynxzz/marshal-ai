#!/usr/bin/env bash
set -Eeuo pipefail

: "${MARSHAL_SSH_TARGET:?Set MARSHAL_SSH_TARGET, e.g. ubuntu@203.0.113.10}"
readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly REMOTE_DIR="${MARSHAL_REMOTE_DIR:-marshal-app}"
readonly SSH_PORT="${MARSHAL_SSH_PORT:-22}"

if [[ ! "$REMOTE_DIR" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "MARSHAL_REMOTE_DIR contains unsupported characters: $REMOTE_DIR" >&2
  exit 2
fi

ssh_args=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$SSH_PORT")
if [[ -n "${MARSHAL_SSH_KEY:-}" ]]; then
  ssh_args+=(-i "$MARSHAL_SSH_KEY")
fi

printf 'Uploading Marshal to %s:%s ...\n' "$MARSHAL_SSH_TARGET" "$REMOTE_DIR"
COPYFILE_DISABLE=1 tar \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='.pytest_cache' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='artifacts' \
  -C "$APP_ROOT" -czf - . |
  ssh "${ssh_args[@]}" "$MARSHAL_SSH_TARGET" \
    "mkdir -p '$REMOTE_DIR' && tar -xzf - -C '$REMOTE_DIR'"

ssh "${ssh_args[@]}" "$MARSHAL_SSH_TARGET" \
  "cd '$REMOTE_DIR' && printf 'remote app: ' && pwd && test -f pyproject.toml && test -f scripts/check_carla.py"
echo "Upload complete."
