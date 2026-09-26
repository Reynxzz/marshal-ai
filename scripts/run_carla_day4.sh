#!/usr/bin/env bash
# Day 4: the faulty-car case. One healthy car must PASS; one with a dark lamp must FAIL
# because the camera contradicts healthy telemetry.
#
#   bash scripts/run_carla_day4.sh
#   MARSHAL_DAY4_STEPS=1,3 bash scripts/run_carla_day4.sh
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly RUNTIME_PATH="$APP_ROOT/src:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla"
readonly STEPS="${MARSHAL_DAY4_STEPS:-1}"
viewer_was_active=false

cd "$APP_ROOT"
export PYTHONPATH="$RUNTIME_PATH"
export MARSHAL_LLM=gemini MARSHAL_DRIVING=carla
: "${GOOGLE_APPLICATION_CREDENTIALS:=$HOME/.config/marshal/sa-gemini-carla.json}"
export GOOGLE_APPLICATION_CREDENTIALS
export GOOGLE_GENAI_USE_VERTEXAI="${GOOGLE_GENAI_USE_VERTEXAI:-TRUE}"
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-id-rd-ca-hanif-ali}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

test -f "$GOOGLE_APPLICATION_CREDENTIALS" || {
  echo "no Gemini credentials at $GOOGLE_APPLICATION_CREDENTIALS" >&2; exit 1; }
test -x /opt/carla/CarlaUE4.sh || {
  echo "/opt/carla is not installed; run scripts/prepare_carla_host.sh first" >&2; exit 1; }

# The routine puts the world in synchronous mode and spawns its own cameras; the viewer is
# an async client holding a camera, so it is stopped for the run and restored afterwards.
if systemctl is-active --quiet carla-viewer 2>/dev/null; then
  viewer_was_active=true
  sudo systemctl stop carla-viewer
fi
restore_viewer() {
  if "$viewer_was_active"; then
    sudo systemctl reset-failed carla-viewer 2>/dev/null || true
    sudo systemctl restart carla-viewer || true
  fi
}
trap restore_viewer EXIT

if ! timeout 30 python3 scripts/check_carla.py >/dev/null 2>&1; then
  echo "CARLA not answering; restarting the service..." >&2
  sudo systemctl restart carla
fi
python3 scripts/ensure_carla_map.py --map Town05 --wait-seconds 600

python3 scripts/demo_carla_day4.py --steps "$STEPS"
