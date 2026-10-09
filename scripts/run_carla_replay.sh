#!/usr/bin/env bash
# Film a recorded run: replay it on the CARLA server with a camera chasing the car, into an MP4.
#
#   bash scripts/run_carla_replay.sh                 # the latest recording
#   bash scripts/run_carla_replay.sh scene1-20261009-201500 --follow VIN-S1
#
# Extra arguments go to replay_carla.py (--follow, --speed, --start, --duration, ...).
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly RECORDINGS=/var/lib/carla/recordings
viewer_was_active=false

cd "$APP_ROOT"
export PYTHONPATH="$APP_ROOT/src:$APP_ROOT/scripts:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla"

name="${1:-}"
if [[ -z "$name" || "$name" == --* ]]; then
  latest=$(sudo ls -t "$RECORDINGS" 2>/dev/null | grep '\.log$' | head -1 || true)
  [[ -n "$latest" ]] || { echo "no recordings in $RECORDINGS" >&2; exit 1; }
  name="${latest%.log}"
else
  shift
fi

python3 -c "import imageio_ffmpeg" 2>/dev/null || {
  echo "imageio-ffmpeg missing: sudo pip3 install --target /opt/marshal-runtime/vendor imageio-ffmpeg" >&2
  exit 1; }

# The replay steps the world in synchronous mode; the viewer's camera would fight it.
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

# Load the map up front: CARLA falls back to Town10 after a restart, and a map load inside the
# replay outlasts the client timeout.
python3 scripts/ensure_carla_map.py --map Town05 --wait-seconds 600
python3 scripts/replay_carla.py --name "$name" "$@"
