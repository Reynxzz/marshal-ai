#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly OUTPUT="${MARSHAL_DAY2_OUTPUT:-/tmp/marshal-day2}"
readonly RUNTIME_PATH="$APP_ROOT/src:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla"
viewer_was_active=false

cd "$APP_ROOT"
export PYTHONPATH="$RUNTIME_PATH"

if systemctl is-active --quiet carla-viewer 2>/dev/null; then
  viewer_was_active=true
  sudo systemctl stop carla-viewer
fi

source "$APP_ROOT/scripts/carla_recording.sh"
restore_viewer() {
  stop_recording
  if ! carla_healthy; then
    echo "Recovering CARLA before viewer restoration..." >&2
    sudo systemctl restart carla || true
    wait_and_load_map || true
  fi
  if "$viewer_was_active"; then
    sudo systemctl reset-failed carla-viewer 2>/dev/null || true
    sudo systemctl restart carla-viewer || true
  fi
}
trap restore_viewer EXIT

carla_healthy() {
  timeout 15 python3 scripts/check_carla.py --host localhost --port 2000 >/dev/null 2>&1
}

wait_and_load_map() {
  python3 scripts/ensure_carla_map.py --map "${MARSHAL_CARLA_MAP:-Town05}" --wait-seconds 600
}

recover_if_needed() {
  if carla_healthy; then
    return
  fi
  echo "CARLA API unhealthy after phase; restarting service..." >&2
  sudo systemctl restart carla
  wait_and_load_map
}

rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"
recover_if_needed
wait_and_load_map
start_recording day2

# Fixed-view process isolation avoids camera relocation/lifecycle instability while
# remaining cheap on a healthy g4dn.xlarge. Restarts occur only when health fails.
for view in front rear left right; do
  echo "=== gate view: $view ==="
  timeout 120 python3 scripts/demo_carla_day2.py \
    --phase gate --view "$view" --output "$OUTPUT"
  recover_if_needed
done

echo "=== track lighting steps 1-4 ==="
timeout 240 python3 scripts/demo_carla_day2.py \
  --phase track --output "$OUTPUT" --timeout 180
recover_if_needed

test -s "$OUTPUT/manifest.json"
python3 -m json.tool "$OUTPUT/manifest.json"
echo "Day 2 artifacts: $OUTPUT"
