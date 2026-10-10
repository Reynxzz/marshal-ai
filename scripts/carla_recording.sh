# Sourced by the run_carla_*.sh wrappers: record the run on the CARLA server, so it can be filmed
# afterwards with scripts/run_carla_replay.sh. MARSHAL_RECORD=0 skips it.
#
# Start it after the map is loaded (a map load ends a recording) and stop it in the EXIT trap.
# A CARLA restart mid-run also ends it; what was recorded up to then is kept.

recording=false

# Dress Town05's plant road as a car plant (scripts/dress_plant.py) before the recording starts, so the
# filmed replay shows it. MARSHAL_DRESS=0 skips it. A dressing that would block a plant lane is
# removed again by the script itself, and the run goes on undressed rather than failing.
dress_plant() {
  [[ "${MARSHAL_DRESS:-1}" != 0 ]] || return 0
  python3 scripts/dress_plant.py || echo "plant dressing skipped (see above)" >&2
}

start_recording() {
  dress_plant
  [[ "${MARSHAL_RECORD:-1}" != 0 ]] || return 0
  sudo install -d -o carla -g carla -m 755 /var/lib/carla/recordings
  if python3 scripts/carla_recorder.py start --name "$1-$(date +%Y%m%d-%H%M%S)"; then
    recording=true
  fi
}

stop_recording() {
  if "$recording"; then
    python3 scripts/carla_recorder.py stop || true
    recording=false
  fi
}
