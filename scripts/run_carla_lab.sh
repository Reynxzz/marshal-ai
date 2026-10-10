#!/usr/bin/env bash
# Day 3 checkpoint: generate a scenario, run it in CARLA, judge it automatically.
#
#   bash scripts/run_carla_lab.sh                       # flow 6 only
#   MARSHAL_LAB_ADVERSARIAL=12 bash scripts/run_carla_lab.sh   # plus flow 7
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly OUTPUT="${MARSHAL_LAB_OUTPUT:-/tmp/marshal-lab}"
readonly RUNTIME_PATH="$APP_ROOT/src:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla"
readonly ADVERSARIAL="${MARSHAL_LAB_ADVERSARIAL:-0}"
readonly REQUEST="${MARSHAL_LAB_REQUEST:-}"
readonly SOURCE="${MARSHAL_LAB_SOURCE:-}"        # e.g. "assistance case AC-..." for an incident
readonly RELEASE="${MARSHAL_LAB_RELEASE:-}"      # e.g. behavior-agent-1.0: run the release gate last
viewer_was_active=false

cd "$APP_ROOT"
export PYTHONPATH="$RUNTIME_PATH"
# The lab drives CARLA itself; without a key the deterministic stand-ins are used.
export MARSHAL_LLM="${MARSHAL_LLM:-offline}"

test -x /opt/carla/CarlaUE4.sh || {
  echo "/opt/carla is not installed; run scripts/prepare_carla_host.sh first" >&2
  exit 1
}

# The compiled scenario puts the world in synchronous mode and spawns its own actors.
# The viewer is an async client with a camera attached, so it is stopped for the run
# and restored afterwards.
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

carla_healthy() {
  timeout 20 python3 scripts/check_carla.py --host localhost --port 2000 >/dev/null 2>&1
}

if ! carla_healthy; then
  echo "CARLA not answering; restarting the service..." >&2
  sudo systemctl restart carla
fi
python3 scripts/ensure_carla_map.py --map Town05 --wait-seconds 600
start_recording lab

rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"

args=(--output "$OUTPUT" --adversarial "$ADVERSARIAL")
if [[ -n "$REQUEST" ]]; then
  args+=(--request "$REQUEST")
fi
if [[ -n "$SOURCE" ]]; then
  args+=(--source "$SOURCE")
fi
if [[ -n "$RELEASE" ]]; then
  args+=(--release "$RELEASE")
fi

python3 scripts/demo_carla_lab.py "${args[@]}"

test -s "$OUTPUT/manifest.json"
echo "Lab artifacts: $OUTPUT"
