#!/usr/bin/env bash
# Run the CARLA service at a given rendering quality, and keep it across reboots.
#
#   bash scripts/set_carla_quality.sh           # Epic, the default and the setting that works
#   bash scripts/set_carla_quality.sh Low       # back to what the AMI ships with
#
# Why Epic: on CARLA 0.9.16 / Tesla T4, -quality-level=Low segfaults the server (Signal 11) as soon
# as a camera looks towards the horizon over the western part of Town05. Probed one camera at a time,
# at quality Low:
#
#   - heading: yaw 135, 150, 163, 180 and 210 crash; 0, 90, 270 and 345 do not
#   - position: crashes at x 140, 180, 221, 260 and 300, and at y -60, -27 and 10 - so it is not
#     one spot, it is anything far away in that direction
#   - pitch: -70 and -40 (looking down at the road) are fine; -25, 0 and +20 crash. -33 is the edge,
#     about where the far distance enters a 90-degree view
#   - field of view 30 at pitch -15 is fine, i.e. the same direction with the horizon cropped out
#   - moving the sun to the opposite side changes nothing, so it is not the sun disc or glare
#
# At -quality-level=Epic every one of those poses rendered, and all six flows still pass. The crash
# is in Low quality's rendering of distant scenery, not in Marshal. Epic uses more GPU memory (3.6 of
# the T4's 15 GiB with a camera up), which is still well within it.
#
# This writes a systemd drop-in rather than editing the unit, so the AMI's unit stays as built and
# going back is one command. Run it on the CARLA host; it restarts CARLA.
set -Eeuo pipefail

readonly LEVEL="${1:-Epic}"
readonly DROPIN_DIR=/etc/systemd/system/carla.service.d
readonly DROPIN="$DROPIN_DIR/quality.conf"

case "$LEVEL" in
  Low|Epic) ;;
  *) echo "quality must be Low or Epic (got '$LEVEL')" >&2; exit 2 ;;
esac

# The base command, from the unit file as installed, with only the quality changed. Read from the
# file rather than `systemctl cat`, which fails outright if systemd still lists a drop-in that has
# since been removed.
sudo systemctl daemon-reload
unit=$(systemctl show carla -p FragmentPath --value)
[[ -f "$unit" ]] || { echo "carla.service unit file not found" >&2; exit 1; }
base=$(grep -E '^ExecStart=/' "$unit" | tail -1 | cut -d= -f2-)
[[ -n "$base" ]] || { echo "no ExecStart in $unit" >&2; exit 1; }
cmd=$(echo "$base" | sed -E "s/-quality-level=[A-Za-z]+/-quality-level=$LEVEL/")
[[ "$cmd" == *"-quality-level="* ]] || cmd="$cmd -quality-level=$LEVEL"

sudo install -d -m 755 "$DROPIN_DIR"
printf '[Service]\nExecStart=\nExecStart=%s\n' "$cmd" | sudo tee "$DROPIN" >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart carla

running=$(systemctl show carla -p ExecStart --value | grep -oE 'quality-level=[A-Za-z]+' | head -1)
echo "carla.service now runs with $running ($DROPIN)"
