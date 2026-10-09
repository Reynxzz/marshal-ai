#!/usr/bin/env bash
# Demo scene 1, one process per phase.
#
# A camera capture and a running driving loop cannot share a process: inline the loop is the only
# ticker and is blocked waiting for the capture, so no frame arrives; concurrently the server
# segfaults. Separate processes keep them apart, and Firestore carries the records between them.
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly VIN="${MARSHAL_SCENE1_VIN:-VIN-S1}"
viewer_was_active=false

cd "$APP_ROOT"
export PYTHONPATH="$APP_ROOT/src:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla"
export MARSHAL_LLM=gemini MARSHAL_DRIVING=carla
# the phases are separate processes, so the live store has to be shared
export MARSHAL_STORE="${MARSHAL_STORE:-gcp}"
export MARSHAL_ARTIFACTS="${MARSHAL_ARTIFACTS:-gcs}"
export MARSHAL_HISTORY="${MARSHAL_HISTORY:-bq}"
: "${GOOGLE_APPLICATION_CREDENTIALS:=$HOME/.config/marshal/sa-gemini-carla.json}"
export GOOGLE_APPLICATION_CREDENTIALS
export GOOGLE_GENAI_USE_VERTEXAI="${GOOGLE_GENAI_USE_VERTEXAI:-TRUE}"
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-id-rd-ca-hanif-ali}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

test -f "$GOOGLE_APPLICATION_CREDENTIALS" || {
  echo "no Gemini credentials at $GOOGLE_APPLICATION_CREDENTIALS" >&2; exit 1; }
test -x /opt/carla/CarlaUE4.sh || {
  echo "/opt/carla is not installed; run scripts/prepare_carla_host.sh first" >&2; exit 1; }

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
start_recording scene1

before=$(systemctl show carla -p NRestarts --value 2>/dev/null || echo 0)
for phase in release gate track report ship; do
  echo "=== phase: $phase ==="
  # Each phase is a fresh process on purpose; do not merge these into one.
  phase_log=$(mktemp)
  if ! python3 scripts/demo_carla_scene1.py --phase "$phase" --vin "$VIN" | tee "$phase_log"; then
    # The CARLA client can abort while the interpreter shuts down ("terminate called without an
    # active exception"), after the phase has finished and stored its result. That is not a
    # failed phase; only a phase that never reported done is.
    if grep -q "\"phase\": \"$phase\".*\"type\": \"scene1.phase.done\"" "$phase_log"; then
      echo "phase $phase finished, then its process crashed on exit; carrying on" >&2
    else
      rm -f "$phase_log"
      echo "scene 1 failed in the $phase phase" >&2
      exit 1
    fi
  fi
  rm -f "$phase_log"
done
after=$(systemctl show carla -p NRestarts --value 2>/dev/null || echo 0)

echo "=== scene 1 complete ==="
echo "CARLA restarts during the run: $((after - before))"
