#!/usr/bin/env bash
# Flow 1: yard traffic on real CARLA. Cars queue at the line end and are released under the
# controller's own rules, with the 25 m spacing measured in real world coordinates. Then a truck
# is delayed, which goes to Gemini and needs a supervisor's approval.
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly RUNTIME_PATH="$APP_ROOT/src:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla"
readonly OUTPUT="${MARSHAL_YARD_OUTPUT:-/tmp/marshal-yard}"
viewer_was_active=false

cd "$APP_ROOT"
export PYTHONPATH="$RUNTIME_PATH"
export MARSHAL_LLM=gemini MARSHAL_DRIVING=carla
: "${GOOGLE_APPLICATION_CREDENTIALS:=$HOME/.config/marshal/sa-gemini-carla.json}"
export GOOGLE_APPLICATION_CREDENTIALS
export GOOGLE_GENAI_USE_VERTEXAI="${GOOGLE_GENAI_USE_VERTEXAI:-TRUE}"
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-id-rd-ca-qais-jabbar}"
# Gemini in the region nearest the CARLA host (Singapore): a decision call took 0.9 s there against
# 2.4 s in us-central1, and a full answer 2.0 s against 3.5 s, from this side of the world.
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-asia-southeast1}"
export MARSHAL_ARTIFACTS="${MARSHAL_ARTIFACTS:-gcs}"
export MARSHAL_HISTORY="${MARSHAL_HISTORY:-bq}"

test -f "$GOOGLE_APPLICATION_CREDENTIALS" || {
  echo "no Gemini credentials at $GOOGLE_APPLICATION_CREDENTIALS" >&2; exit 1; }
test -x /opt/carla/CarlaUE4.sh || {
  echo "/opt/carla is not installed; run scripts/prepare_carla_host.sh first" >&2; exit 1; }

# Several cars drive at once here; the viewer is an async client holding a camera, so it is
# stopped for the run and restored afterwards.
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
start_recording yard

rm -rf "$OUTPUT"
python3 scripts/demo_carla_yard.py --output "$OUTPUT"
echo "Yard artifacts: $OUTPUT"
