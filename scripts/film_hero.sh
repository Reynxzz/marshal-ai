#!/usr/bin/env bash
# Re-record the CARLA footage for the paper video: one hero car through Marshal's whole flow, filmed live.
#
#   bash scripts/film_hero.sh            # every shoot, in order
#   bash scripts/film_hero.sh plant      # or one of: plant, replay, lamp, help, pack
#
# Shoots, and the video steps they feed (video/paper, section order):
#   plant   VIN-P1, the hero, through line -> gate -> track -> report -> lane C (a forklift staged at the
#           corner) -> Gemini parks it in Y8 -> the late truck's re-plan moves it. VIN-P2 is painted red and
#           fails the gate, then drives to rework. Filmed live from cameras on P1 (chase, top) and P2 (chase),
#           and recorded for a second angle.                       steps 1, 2, 4, 5, 6, 7
#   replay  the plant recording again, chasing P1, with the live run's cars removed first (no copy of the
#           car standing where it ended) and no burned-in labels: the video draws its own.
#   lamp    day 4 at night: VIN-OK's headlights lit, VIN-DARK's dark, then VIN-DARK to the electrical bay,
#           repaired, back for a re-check. Filmed live (front and chase): a replay cannot show headlights
#           on the dressed world.                                  step 3
#   help    day 5: VIN-STUCK stops for a box and a worker, a person chooses wait, the lane clears, it
#           drives on. Filmed live (chase).                        branch H
#   pack    the films, the run logs, Gemini's evidence and the decision notes into one tarball.
#
# Graphics: CARLA runs at -quality-level=Epic (set_carla_quality.sh; Low segfaults on the T4). Replays use the
# cinematic look (low sun, wet concrete, haze, 40 fps). The live runs keep the plant's tested light, sun 30
# (v6-gate measured the gate at 15, 30 and 45): wet, reflective ground there could change what Gemini sees
# at the gate and on the track. Their film cameras match the cinematic camera: post-processing, no blur.
#
# Everything lands in ~/rey-runs/hero-<date>/. Fresh-host gotchas this already handles: the plant world is
# recorded without light data (MARSHAL_RECORD_LIGHTS=0, which otherwise segfaults the server) and the
# records stay in memory (MARSHAL_STORE=memory: the shared Firestore holds other people's test cars).
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly OUT="${MARSHAL_HERO_OUT:-$HOME/rey-runs/hero-$(date +%Y%m%d)}"
readonly NOTES="${MARSHAL_NOTES_DIR:-$HOME/.cache/marshal/recordings}"
mkdir -p "$OUT/live" "$OUT/evidence"
cd "$APP_ROOT"

# the model mix the video describes: 3.8 Flash reasons, 2.5 Flash decides (config.py defaults)
export MARSHAL_MODEL="${MARSHAL_MODEL:-gemini-3.8-flash}"
export MARSHAL_STORE=memory MARSHAL_RECORD_LIGHTS=0
export MARSHAL_FILM_DIR="$OUT/live"
# The plant run is filmed from its recording (replay), not live: live film cameras encoded in the camera
# callback held up the driving loop (fixed since: the writer encodes on its own thread, dropping frames when
# behind), and the replay gives the cinematic look at 40 fps anyway. MARSHAL_HERO_CARS=VIN-P1 films it live too.
# Each 1080p film camera renders every tick at Epic on the T4. Four of them (chase and top on two cars), with
# the cars' own evidence cameras, slowed the plant run to 10 simulated seconds in 11 minutes and timed out a
# driving step (2026-10-18), so the plant run films chase views only (MARSHAL_HERO_VIEWS to change) and
# waits longer for the server.
export CARLA_TIMEOUT_S="${CARLA_TIMEOUT_S:-60}"

shoot_plant() {
  echo "== plant: the hero run" >&2
  MARSHAL_FILM="${MARSHAL_HERO_CARS:-NONE}" MARSHAL_FILM_VIEWS="${MARSHAL_HERO_VIEWS:-chase}" \
  MARSHAL_PARKING=gemini MARSHAL_PARK_MODE=paths \
  MARSHAL_EVIDENCE_DIR="$OUT/evidence" \
  MARSHAL_PLANT_CARS=4 MARSHAL_PLANT_WRONG_COLOUR=2 \
  MARSHAL_PLANT_TRUCKS=T-1:20,T-2:45 MARSHAL_PLANT_LATE_TRUCK=T-1:40 \
  MARSHAL_PLANT_HAZARD="finishing_corner:forklift unloading pallets" MARSHAL_PLANT_STAGE_HAZARD=1 \
  MARSHAL_PLANT_SUN=30 MARSHAL_PLANT_TIMEOUT=1800 \
    bash scripts/run_carla_plant.sh | tee "$OUT/plant.jsonl"
}

shoot_replay() {
  local name
  name=$(sudo ls -t /var/lib/carla/recordings 2>/dev/null | grep '^plant-.*\.log$' | head -1)
  [[ -n "$name" ]] || { echo "no plant recording to replay" >&2; return 1; }
  name="${name%.log}"
  for vin in VIN-P1 VIN-P2; do
    echo "== replay: $name, chasing $vin" >&2
    bash scripts/run_carla_replay.sh "$name" --follow "$vin" --look cinematic \
      --no-labels --no-notes --out "$OUT/replay-$vin.mp4"
  done
  echo "$name" > "$OUT/recording.txt"
}

shoot_lamp() {
  echo "== lamp: day 4, live" >&2
  MARSHAL_RECORD=0 MARSHAL_FILM=VIN-OK,VIN-DARK MARSHAL_FILM_VIEWS=front,chase MARSHAL_DAY4_REWORK=1 \
    bash scripts/run_carla_day4.sh | tee "$OUT/lamp.jsonl"
}

shoot_help() {
  echo "== help: day 5, live" >&2
  MARSHAL_RECORD=0 MARSHAL_FILM=VIN-STUCK MARSHAL_FILM_VIEWS=chase,top \
    bash scripts/run_carla_day5.sh | tee "$OUT/help.jsonl"
}

pack() {
  local name
  name=$(cat "$OUT/recording.txt" 2>/dev/null || true)
  if [[ -n "$name" ]]; then
    cp "$NOTES/$name".* "$OUT/" 2>/dev/null || true       # decisions with the simulator's clock, to cut by
  fi
  ls -la "$OUT" "$OUT/live" >&2
  tar -C "$(dirname "$OUT")" -czf "$OUT.tgz" "$(basename "$OUT")"
  echo "packed: $OUT.tgz" >&2
}

case "${1:-all}" in
  plant) shoot_plant ;;
  replay) shoot_replay ;;
  lamp) shoot_lamp ;;
  help) shoot_help ;;
  pack) pack ;;
  all) shoot_plant; shoot_replay; shoot_lamp; shoot_help; pack ;;
  *) echo "usage: $0 [plant|replay|lamp|help|pack|all]" >&2; exit 2 ;;
esac
