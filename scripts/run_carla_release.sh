#!/usr/bin/env bash
# Demo scenes 4 and 5 on CARLA: a search finds a failure, it joins the regression suite, and the
# release gate runs the whole library against the driving system and blocks the release.
#
#   1. The lab (run_carla_lab.sh) generates the design's example scenario with Gemini, runs and
#      judges it on CARLA, and an engineer puts it in the library.
#   2. The adversarial search varies it on CARLA until a variant fails, shrinks the failure and adds
#      it to the library as a regression test, with its failure report.
#   3. The release gate runs every library test against behavior-agent-1.0. Each report is written to
#      BigQuery history, and "what changed" is read back from the release evaluated before it.
#
#   bash scripts/run_carla_release.sh
#   MARSHAL_LAB_ADVERSARIAL=24 bash scripts/run_carla_release.sh       # a larger search budget
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly OUTPUT="${MARSHAL_RELEASE_OUTPUT:-/tmp/marshal-release}"
cd "$APP_ROOT"

# Set here as run_incident_to_test.sh sets them: the host's .env names a laptop key path.
: "${GOOGLE_APPLICATION_CREDENTIALS:=$HOME/.config/marshal/sa-gemini-carla.json}"
export GOOGLE_APPLICATION_CREDENTIALS
export GOOGLE_GENAI_USE_VERTEXAI="${GOOGLE_GENAI_USE_VERTEXAI:-TRUE}"
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-id-rd-ca-hanif-ali}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export MARSHAL_HISTORY="${MARSHAL_HISTORY:-bq}"
test -f "$GOOGLE_APPLICATION_CREDENTIALS" || {
  echo "no Gemini credentials at $GOOGLE_APPLICATION_CREDENTIALS" >&2; exit 1; }

MARSHAL_LLM="${MARSHAL_LLM:-gemini}" MARSHAL_LAB_ADVERSARIAL="${MARSHAL_LAB_ADVERSARIAL:-16}" \
  MARSHAL_LAB_RELEASE="${MARSHAL_RELEASE:-behavior-agent-1.0}" MARSHAL_LAB_OUTPUT="$OUTPUT" \
  bash scripts/run_carla_lab.sh

python3 - "$OUTPUT/manifest.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
search, release = m.get("adversarial") or {}, m.get("release") or {}
regression = search.get("regression_id")
# Scene 5 is shown when the failure the search found is in the suite and is what blocks the release.
ok = bool(regression) and release.get("blocked") and regression in release.get("failed", {})
print(json.dumps({"type": "release.done", "regression_test": regression,
                  "summary": release.get("summary"), "previous_release": release.get("previous_release"),
                  "failure_report": search.get("report"),
                  "result": "success" if ok else "not demonstrated"}, sort_keys=True))
sys.exit(0 if ok else 2)
PY
