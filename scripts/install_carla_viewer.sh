#!/usr/bin/env bash
set -Eeuo pipefail

readonly INSTALL_DIR=/opt/carla-viewer
readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly WHEEL="$(find /opt/carla/PythonAPI/carla/dist -name '*cp310*.whl' -print -quit)"

if [[ -z "$WHEEL" ]]; then
  echo "CARLA cp310 wheel not found under /opt/carla" >&2
  exit 1
fi

sudo install -d -m 0755 "$INSTALL_DIR" "$INSTALL_DIR/vendor"
sudo install -m 0644 "$APP_ROOT/src/marshal_ai/carla_viewer.py" "$INSTALL_DIR/carla_viewer.py"
sudo python3 -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  --upgrade \
  --target "$INSTALL_DIR/vendor" \
  "$WHEEL" \
  'pillow==11.3.0'
sudo chown -R root:root "$INSTALL_DIR"

sudo tee /etc/systemd/system/carla-viewer.service >/dev/null <<'UNIT'
[Unit]
Description=Low-resource private CARLA browser viewer
After=network-online.target carla.service
Wants=network-online.target carla.service

[Service]
Type=simple
User=carla
Group=carla
StateDirectory=carla-viewer
WorkingDirectory=/opt/carla-viewer
Environment=HOME=/var/lib/carla-viewer
Environment=PYTHONPATH=/opt/carla-viewer/vendor
Environment=PYTHONUNBUFFERED=1
# spawn-index 155 is exactly line_end on Town05 (207.3, -13.9, yaw -90.7), so the camera
# overlooks the start lane. Town05 has 302 spawn points. If the viewer starts while the
# server is still on another map (Town10HD_Opt has only 155) it exits with
# "spawn-index must be between 0 and 154"; restart it after Town05 is loaded rather than
# lowering the index, which would move the camera hundreds of metres away.
ExecStart=/usr/bin/python3 /opt/carla-viewer/carla_viewer.py --bind 127.0.0.1 --http-port 8080 --width 640 --height 360 --fps 2 --jpeg-quality 60 --spawn-index 155
Restart=on-failure
RestartSec=10
TimeoutStopSec=15
Nice=10
CPUQuota=30%
MemoryMax=256M
OOMScoreAdjust=500
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable carla-viewer.service
sudo systemctl restart carla-viewer.service
sudo systemctl status carla-viewer.service --no-pager
