#!/usr/bin/env bash
# Every flow on the real CARLA server, in order: one command to check that a change broke nothing.
#
#   bash scripts/run_carla_all.sh                    # every flow once, then film each recording
#   MARSHAL_ALL_RUNS=3 bash scripts/run_carla_all.sh # M1: three passes in a row
#   MARSHAL_ALL_FLOWS="yard day4" bash scripts/run_carla_all.sh
#   MARSHAL_ALL_FILM=0 bash scripts/run_carla_all.sh # skip filming (saves ~1 min a flow)
#
# Run it on the CARLA host, after `source ~/marshal_env.sh` (see OPERATOR.md in marshal-tf).
#
# CARLA is restarted before every flow on purpose, so no flow inherits another's world: a run that
# ended badly can leave synchronous mode on with nothing ticking, or actors in the way. That
# deliberate restart is not a crash. Crashes are counted separately, from systemd's NRestarts, which
# only goes up when the server died and systemd brought it back.
#
# A flow that fails does not stop the rest; the summary at the end says which did. The exit code is
# 0 only if every flow passed in every pass and the server never crashed.
set -Euo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly RUNS="${MARSHAL_ALL_RUNS:-1}"
readonly FLOWS="${MARSHAL_ALL_FLOWS:-yard day5 day2 day4 scene1 plant lab}"
readonly FILM="${MARSHAL_ALL_FILM:-1}"
readonly RECORDINGS=/var/lib/carla/recordings
readonly OUT="${MARSHAL_ALL_OUT:-$HOME/marshal-runs/$(date +%Y%m%d-%H%M%S)}"

cd "$APP_ROOT"
export PYTHONPATH="$APP_ROOT/src:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla"
mkdir -p "$OUT"

# What each flow demonstrates, for the summary.
declare -A WHAT=(
  [yard]="flow 1: release rules, truck delay re-planned by Gemini"
  [day5]="flow 2: stuck car, 'wait' recommended near a worker"
  [day2]="flow 3: gate views, track lighting steps"
  [day4]="flows 4+5: dark headlight caught, routed to rework"
  [scene1]="journey: line, gate, track, report, yard, shipped"
  [plant]="scene 1 with three cars at once, slots by truck"
  [lab]="flows 6+7: scenario generated, run, judged, searched"
)

restarts() { systemctl show carla -p NRestarts --value 2>/dev/null || echo 0; }

fresh_server() {
  # A clean server for the next flow: restart, wait for the RPC port, load the map. The wrappers
  # load the map too, but a restart lands on Town10HD_Opt and the first load takes a minute, so
  # doing it here keeps that time out of the flow's own timeouts.
  sudo systemctl restart carla
  for _ in $(seq 1 60); do
    sudo ss -tln 2>/dev/null | grep -q ":2000" && break
    sleep 5
  done
  sleep 10
  python3 scripts/ensure_carla_map.py --map Town05 --wait-seconds 600 >/dev/null
}

summary="$OUT/summary.txt"
printf "%-4s %-7s %-6s %-6s %-8s %s\n" "pass" "flow" "result" "secs" "crashes" "what the flow reported" >"$summary"

failures=0
total_crashes=0
started_at=$(date +%s)

for pass in $(seq 1 "$RUNS"); do
  for flow in $FLOWS; do
    script="scripts/run_carla_${flow}.sh"
    if [[ ! -f "$script" ]]; then
      echo "unknown flow '$flow' (no $script)" >&2
      failures=$((failures + 1))
      continue
    fi

    log="$OUT/pass${pass}-${flow}.log"
    echo "=== pass $pass/$RUNS: $flow (${WHAT[$flow]:-}) ==="
    if ! fresh_server; then
      echo "CARLA did not come back before $flow" | tee "$log" >&2
      printf "%-4s %-7s %-6s %-6s %-8s %s\n" "$pass" "$flow" "FAIL" "-" "-" "server did not start" >>"$summary"
      failures=$((failures + 1))
      continue
    fi

    before=$(restarts)
    t0=$(date +%s)
    bash "$script" >"$log" 2>&1
    code=$?
    secs=$(( $(date +%s) - t0 ))
    crashed=$(( $(restarts) - before ))
    total_crashes=$((total_crashes + crashed))

    # What the flow said about itself: its one-line "<flow>.done" event, or scene 1's result line.
    # Only single-line JSON counts - several demos also print a pretty-printed manifest, whose
    # fragments ("verdict": "PASS") say nothing about the run as a whole.
    last=$(grep -E '^\{.*"type": "[a-z0-9_]+\.done"|^\{.*"result": "[a-z_.]+"|^result: ' "$log" \
           | tail -1 | grep -oE '"result": "[^"]+"|"passed": (true|false)|^result: .*|"type": "[a-z0-9_.]+"' \
           | head -1)
    if [[ $code -eq 0 && $crashed -eq 0 ]]; then
      result=PASS
    else
      result=FAIL
      failures=$((failures + 1))
    fi
    printf "%-4s %-7s %-6s %-6s %-8s %s\n" "$pass" "$flow" "$result" "$secs" "$crashed" "${last:-exit $code}" >>"$summary"
    echo "    $result in ${secs}s (exit $code, crashes $crashed)  log: $log"
  done
done

# Film what was recorded during this run, newest last. Filming is after every flow so that a replay
# crashing the server - which has happened - cannot fail a flow.
if [[ "$FILM" != 0 ]]; then
  echo "=== filming recordings ==="
  films="$OUT/films.txt"
  : >"$films"
  # recordings made since this script started
  while read -r rec; do
    name="${rec%.log}"
    fresh_server || { echo "CARLA did not come back to film $name" >>"$films"; continue; }
    if bash scripts/run_carla_replay.sh "$name" --out "$OUT/${name}.mp4" >"$OUT/film-${name}.log" 2>&1 \
       && [[ -s "$OUT/${name}.mp4" ]]; then
      echo "filmed $name: $OUT/${name}.mp4" >>"$films"
    else
      echo "could not film $name (see film-${name}.log)" >>"$films"
    fi
  done < <(sudo find "$RECORDINGS" -maxdepth 1 -name '*.log' -newermt "@$started_at" -printf '%T@ %f\n' 2>/dev/null \
           | sort -n | cut -d' ' -f2)
  cat "$films"
fi

echo
echo "=== summary ($OUT) ==="
cat "$summary"
echo
echo "flows failed: $failures   CARLA crashes: $total_crashes   took: $(( ($(date +%s) - started_at) / 60 )) min"
[[ $failures -eq 0 && $total_crashes -eq 0 ]]
