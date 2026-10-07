#!/usr/bin/env bash
# Day 5: the stuck car. A box in the lane with a worker beside it; the driving system stops and
# asks, Gemini ranks the offered options, and a person in the scene forces a wait recommendation.
#
#   bash scripts/run_carla_day5.sh
#   MARSHAL_DAY5_NO_WORKER=1 bash scripts/run_carla_day5.sh   # box only
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly RUNTIME_PATH="$APP_ROOT/src:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla"
readonly OUTPUT="${MARSHAL_DAY5_OUTPUT:-/tmp/marshal-day5}"
viewer_was_active=false

cd "$APP_ROOT"
export PYTHONPATH="$RUNTIME_PATH"
export MARSHAL_LLM=gemini MARSHAL_DRIVING=carla
: "${GOOGLE_APPLICATION_CREDENTIALS:=$HOME/.config/marshal/sa-gemini-carla.json}"
export GOOGLE_APPLICATION_CREDENTIALS
export GOOGLE_GENAI_USE_VERTEXAI="${GOOGLE_GENAI_USE_VERTEXAI:-TRUE}"
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-id-rd-ca-hanif-ali}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
# store the photos behind the verdicts, so the gs:// URIs in records resolve
export MARSHAL_ARTIFACTS="${MARSHAL_ARTIFACTS:-gcs}"
# and keep the trail of state changes behind them
export MARSHAL_HISTORY="${MARSHAL_HISTORY:-bq}"

test -f "$GOOGLE_APPLICATION_CREDENTIALS" || {
  echo "no Gemini credentials at $GOOGLE_APPLICATION_CREDENTIALS" >&2; exit 1; }
test -x /opt/carla/CarlaUE4.sh || {
  echo "/opt/carla is not installed; run scripts/prepare_carla_host.sh first" >&2; exit 1; }

# The run puts the world in synchronous mode and spawns its own camera; the viewer is an async
# client holding one, so it is stopped for the run and restored afterwards.
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

rm -rf "$OUTPUT"
args=(--output "$OUTPUT")
if [[ -n "${MARSHAL_DAY5_NO_WORKER:-}" ]]; then
  args+=(--no-with-worker)
fi
python3 scripts/demo_carla_day5.py "${args[@]}"
echo "Day 5 artifacts: $OUTPUT"
