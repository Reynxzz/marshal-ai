#!/usr/bin/env bash
# Several cars through the whole plant at once: line, gate, track, report, yard, shipped.
# See scripts/demo_carla_plant.py.
#
#   bash scripts/run_carla_plant.sh
#   MARSHAL_PLANT_CARS=4 MARSHAL_PLANT_STEPS=1,3 bash scripts/run_carla_plant.sh
#   MARSHAL_PLANT_CARS=6 MARSHAL_PLANT_TRUCKS=T-1:20,T-2:45 MARSHAL_PLANT_LATE_TRUCK=T-1:40 \
#     bash scripts/run_carla_plant.sh                   # flow 1 in full, with the late truck
#   MARSHAL_PARKING=gemini bash scripts/run_carla_plant.sh   # Gemini parks each car (driving/parking.py)
#   MARSHAL_PLANT_HAZARD="finishing_corner:forklift unloading pallets" bash scripts/run_carla_plant.sh
#                                                       # Gemini routes cars along lane C instead
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
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-id-rd-ca-qais-jabbar}"
# Gemini in the region nearest the CARLA host (Singapore): a decision call took 0.9 s there against
# 2.4 s in us-central1, and a full answer 2.0 s against 3.5 s, from this side of the world.
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-asia-southeast1}"
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
python3 scripts/ensure_carla_map.py --map "${MARSHAL_CARLA_MAP:-Town05}" --wait-seconds 600
start_recording plant

# Flow 1 in full: MARSHAL_PLANT_LATE_TRUCK=T-1:40 delays that truck once every car is parked, and
# Gemini re-plans the slots (the cars drive to them once a supervisor approves)
extra=()
[[ -n "${MARSHAL_PLANT_TRUCKS:-}" ]] && extra+=(--trucks "$MARSHAL_PLANT_TRUCKS")
[[ -n "${MARSHAL_PLANT_LATE_TRUCK:-}" ]] && extra+=(--late-truck "$MARSHAL_PLANT_LATE_TRUCK")
[[ -n "${MARSHAL_PLANT_TIMEOUT:-}" ]] && extra+=(--timeout "$MARSHAL_PLANT_TIMEOUT")
# MARSHAL_PLANT_HAZARD="finishing_corner:forklift unloading pallets": Gemini routes cars around it
[[ -n "${MARSHAL_PLANT_HAZARD:-}" ]] && extra+=(--hazard "$MARSHAL_PLANT_HAZARD")
# MARSHAL_PLANT_STAGE_HAZARD=1: and put it in the world beside the road, for the film
[[ "${MARSHAL_PLANT_STAGE_HAZARD:-0}" != 0 ]] && extra+=(--stage-hazards)
python3 scripts/demo_carla_plant.py --cars "$CARS" --steps "$STEPS" --wrong-colour "$WRONG" "${extra[@]}"
