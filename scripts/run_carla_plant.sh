#!/usr/bin/env bash
# Several cars through the whole plant at once: line, gate, track, report, yard, shipped.
# See scripts/demo_carla_plant.py.
#
#   bash scripts/run_carla_plant.sh
#   MARSHAL_PLANT_CARS=4 MARSHAL_PLANT_STEPS=1,3 bash scripts/run_carla_plant.sh
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly RUNTIME_PATH="$APP_ROOT/src:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla"
readonly STEPS="${MARSHAL_PLANT_STEPS:-1}"
readonly CARS="${MARSHAL_PLANT_CARS:-3}"
# MARSHAL_PLANT_WRONG_COLOUR=2: car 2 is painted red against a blue build sheet (demo scene 2)
readonly WRONG="${MARSHAL_PLANT_WRONG_COLOUR:-0}"
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
# the cars' records, so the run can be inspected afterwards (build_evidence_page.py)
export MARSHAL_STORE="${MARSHAL_STORE:-gcp}"

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
python3 scripts/ensure_carla_map.py --map Town05 --wait-seconds 600
start_recording plant

python3 scripts/demo_carla_plant.py --cars "$CARS" --steps "$STEPS" --wrong-colour "$WRONG"
