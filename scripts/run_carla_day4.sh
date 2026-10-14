#!/usr/bin/env bash
# Day 4: the faulty-car case. One healthy car must PASS; one with a dark lamp must FAIL
# because the camera contradicts healthy telemetry.
#
#   bash scripts/run_carla_day4.sh
#   MARSHAL_DAY4_STEPS=1,3 bash scripts/run_carla_day4.sh
#   MARSHAL_DAY4_REWORK=1 bash scripts/run_carla_day4.sh    # and on through repair to PASS
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
# Gemini in the region nearest the CARLA host (Singapore): a decision call took 0.9 s there against
# 2.4 s in us-central1, and a full answer 2.0 s against 3.5 s, from this side of the world.
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-asia-southeast1}"
# store the photos behind the verdicts, so the gs:// URIs in records resolve
export MARSHAL_ARTIFACTS="${MARSHAL_ARTIFACTS:-gcs}"
# and keep the trail of state changes behind them
export MARSHAL_HISTORY="${MARSHAL_HISTORY:-bq}"

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
source "$APP_ROOT/scripts/carla_recording.sh"
restore_viewer() {
  stop_recording
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
python3 scripts/ensure_carla_map.py --map "${MARSHAL_CARLA_MAP:-Town05}" --wait-seconds 600
start_recording day4

# MARSHAL_DAY4_REWORK=1 goes on through flow 5: repair, re-inspection, report PASS
python3 scripts/demo_carla_day4.py --steps "$STEPS" ${MARSHAL_DAY4_REWORK:+--rework} \
  ${MARSHAL_DAY4_SUN:+--sun-altitude "$MARSHAL_DAY4_SUN"} "$@"
