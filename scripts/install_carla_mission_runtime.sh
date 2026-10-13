#!/usr/bin/env bash
set -Eeuo pipefail

readonly INSTALL_DIR=/opt/marshal-runtime
readonly WHEEL="$(find /opt/carla/PythonAPI/carla/dist -name '*cp310*.whl' -print -quit)"

if [[ -z "$WHEEL" ]]; then
  echo "CARLA cp310 wheel not found under /opt/carla" >&2
  exit 1
fi

sudo install -d -m 0755 "$INSTALL_DIR/vendor"
sudo python3 -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  --upgrade \
  --target "$INSTALL_DIR/vendor" \
  "$WHEEL" \
  'numpy==1.26.4' \
  'networkx==3.4.2' \
  'shapely==2.0.7' \
  'pydantic==2.11.7' \
  'pydantic-core==2.33.2' \
  'annotated-types==0.8.0' \
  'typing-inspection==0.4.4' \
  'typing-extensions==4.16.0' \
  'PyYAML==6.0.2' \
  'pillow==10.4.0' \
  'python-dotenv' \
  'imageio-ffmpeg' \
  google-genai google-cloud-firestore google-cloud-storage google-cloud-bigquery google-cloud-pubsub
# pillow: labels in the filmed replays (scripts/film_labels.py). python-dotenv: marshal_ai.config,
# which scripts/check_carla.py imports, so without it every run takes CARLA for dead and restarts it.
# imageio-ffmpeg: the replay films. The Google SDKs: Gemini and the shared store; the AMI has none.
sudo chown -R root:root "$INSTALL_DIR"

PYTHONPATH="$INSTALL_DIR/vendor:/opt/carla/PythonAPI/carla" python3 - <<'PY'
import carla
import networkx
import numpy
import pydantic
import shapely
import yaml
from agents.navigation.behavior_agent import BehaviorAgent

print(
    "mission runtime ready:",
    f"carla={carla.Client('localhost', 2000).get_client_version()}",
    f"numpy={numpy.__version__}",
    f"networkx={networkx.__version__}",
    f"pydantic={pydantic.__version__}",
    f"shapely={shapely.__version__}",
)
PY
