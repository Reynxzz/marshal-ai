#!/usr/bin/env bash
# Extra CARLA servers on the one GPU, for lab runs in parallel (M3, "8 runs in parallel").
#
# A compiled scenario puts its world in synchronous mode and ticks it, so two runs cannot share a
# server. Each extra server is its own process on its own ports: server i (1..N) answers RPC on
# 2000 + 10*i and streams on the port after, so they never meet the shared one on 2000 or each other.
# They run as systemd instances of carla-pool@<rpc port>, as the carla user, at the quality the
# shared service runs at, and restart on a crash like it does. The shared `carla` service is not
# touched, so whoever is using it is not disturbed.
#
# Memory is the limit on a g4dn.xlarge (16 GB, 4 vCPU, T4 16 GB), not the GPU. One server on Town05
# holds about 3 GB of its own plus about 4.7 GB of mapped map files, and more while it loads a map.
# The first try started three servers at once: the kernel ran out of memory while they loaded, the
# units restarted, and the host stopped answering SSH until it was rebooted. So:
#  - servers start one at a time, each only after the one before has its map loaded;
#  - each starts only if MemAvailable is at least MARSHAL_POOL_MIN_FREE_MB (default 5000);
#  - each unit is capped at MARSHAL_POOL_MEM_MAX (default 6G), so a runaway server is killed in its
#    own cgroup and the host survives;
#  - pool servers are the kernel's first choice to kill (OOMScoreAdjust=800), never the shared one;
#  - a unit that keeps failing stops restarting (2 tries in 5 minutes).
#
#   bash scripts/carla_pool.sh up 3        # servers on 2010, 2020, 2030, each on Town05
#   bash scripts/carla_pool.sh status
#   bash scripts/carla_pool.sh down        # stop every extra server
#   bash scripts/carla_pool.sh ports 3     # 2010,2020,2030 (for --ports)
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly UNIT=/etc/systemd/system/carla-pool@.service
readonly MAP="${MARSHAL_CARLA_MAP:-Town05}"
readonly MIN_FREE_MB="${MARSHAL_POOL_MIN_FREE_MB:-5000}"
readonly MEM_MAX="${MARSHAL_POOL_MEM_MAX:-6G}"
export PYTHONPATH="$APP_ROOT/src:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla"

ports() { local n="$1" i; for ((i = 1; i <= n; i++)); do printf '%s' "$((2000 + 10 * i))"; ((i < n)) && printf ','; done; echo; }

quality() {
  # the shared service's quality, so a pool run renders as a shared run does (Low segfaults on
  # westward views; see set_carla_quality.sh)
  systemctl cat carla 2>/dev/null | grep -o 'quality-level=[A-Za-z]*' | tail -1 | cut -d= -f2 || echo Epic
}

install_unit() {
  sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=Extra CARLA server for parallel lab runs (RPC port %i)
After=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=2

[Service]
Type=simple
User=carla
Group=carla
WorkingDirectory=/opt/carla
Environment=HOME=/var/lib/carla
ExecStart=/bin/bash -c 'exec /opt/carla/CarlaUE4.sh -RenderOffScreen -nosound -quality-level=$(quality) -carla-rpc-port=%i -carla-streaming-port=\$((%i + 1))'
Restart=on-failure
RestartSec=15
TimeoutStopSec=60
MemoryMax=$MEM_MAX
OOMScoreAdjust=800
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=/opt/carla /var/lib/carla
EOF
  sudo systemctl daemon-reload
}

free_mb() { awk '/MemAvailable/ {print int($2 / 1024)}' /proc/meminfo; }

up() {
  local n="${1:?how many extra servers}" port started=0
  install_unit
  for port in $(ports "$n" | tr ',' ' '); do
    if systemctl is-active --quiet "carla-pool@$port"; then
      started=$((started + 1)); continue
    fi
    if (( $(free_mb) < MIN_FREE_MB )); then
      echo "not starting carla-pool@$port: $(free_mb) MB free, need $MIN_FREE_MB" >&2
      break
    fi
    sudo systemctl start "carla-pool@$port"
    for _ in $(seq 1 60); do
      sudo ss -tln 2>/dev/null | grep -q ":$port " && break
      sleep 5
    done
    # the map load is the peak; the next server waits for it
    if ! python3 "$APP_ROOT/scripts/ensure_carla_map.py" --port "$port" --map "$MAP" --wait-seconds 600; then
      echo "carla-pool@$port did not load $MAP; stopping it" >&2
      sudo systemctl stop "carla-pool@$port"
      break
    fi
    started=$((started + 1))
    echo "carla-pool@$port ready on $MAP, $(free_mb) MB free"
  done
  echo "extra servers running: $started of $n asked; use --ports $(ports "$started")"
  status
}

down() {
  local units
  units=$(systemctl list-units --no-legend --plain 'carla-pool@*' | awk '{print $1}')
  [[ -z "$units" ]] || sudo systemctl stop $units
  status
}

status() {
  local units
  units=$(systemctl list-units --no-legend --plain --all 'carla-pool@*' | awk '{print $1, $3, $4}')
  echo "${units:-no extra CARLA servers}"
  echo "memory free: $(free_mb) MB"
  nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader
}

case "${1:-status}" in
  up) up "${2:-}" ;;
  down) down ;;
  status) status ;;
  ports) ports "${2:?how many}" ;;
  *) echo "usage: $0 up N | down | status | ports N" >&2; exit 2 ;;
esac
