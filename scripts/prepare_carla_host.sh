#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly RUNTIME_PATH="$APP_ROOT/src:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla"

cd "$APP_ROOT"
test -x /opt/carla/CarlaUE4.sh || { echo "/opt/carla is not installed" >&2; exit 1; }

bash scripts/install_carla_mission_runtime.sh
# Low quality segfaults the server when a camera faces the far west of Town05; see the script.
bash scripts/set_carla_quality.sh Epic
export PYTHONPATH="$RUNTIME_PATH"
python3 scripts/ensure_carla_map.py --map Town05 --wait-seconds 600
python3 scripts/check_carla.py
bash scripts/install_carla_viewer.sh
curl --fail --silent --show-error --retry 10 --retry-delay 1 --retry-connrefused \
  http://127.0.0.1:8080/healthz
echo
echo "Marshal CARLA host preparation complete."
