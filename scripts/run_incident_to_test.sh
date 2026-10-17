#!/usr/bin/env bash
# The design's loop between operations and the test lab, on CARLA: a stuck car becomes a test.
#
#   1. Demo scene 3 (run_carla_day5.sh): a box in the lane with a worker beside it stops the car;
#      the assistance desk recommends waiting, a supervisor decides, and the case goes to the lab.
#   2. The lab (run_carla_lab.sh), on that case: Gemini writes a scenario spec, it is validated,
#      checked fair and dry-run, run and judged on CARLA, and reviewed into the test library
#      tagged from-incident.
#
#   bash scripts/run_incident_to_test.sh
#   MARSHAL_INCIDENT_CASE=/tmp/marshal-day5/case.json bash scripts/run_incident_to_test.sh  # reuse a case
set -Eeuo pipefail

readonly APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
readonly CASE="${MARSHAL_INCIDENT_CASE:-}"
readonly OUTPUT="${MARSHAL_INCIDENT_OUTPUT:-/tmp/marshal-incident}"
cd "$APP_ROOT"

# Gemini credentials for the lab step, set as run_carla_day5.sh sets them. run_carla_lab.sh leaves
# them to .env, and the .env on the host was copied from a laptop: its key path does not exist there.
: "${GOOGLE_APPLICATION_CREDENTIALS:=$HOME/.config/marshal/sa-gemini-carla.json}"
export GOOGLE_APPLICATION_CREDENTIALS
export GOOGLE_GENAI_USE_VERTEXAI="${GOOGLE_GENAI_USE_VERTEXAI:-TRUE}"
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-id-rd-ca-hanif-ali}"
# Gemini in the region nearest the CARLA host (Singapore): a decision call took 0.9 s there against
# 2.4 s in us-central1, and a full answer 2.0 s against 3.5 s, from this side of the world.
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-asia-southeast1}"
test -f "$GOOGLE_APPLICATION_CREDENTIALS" || {
  echo "no Gemini credentials at $GOOGLE_APPLICATION_CREDENTIALS" >&2; exit 1; }

case_file="$CASE"
if [[ -z "$case_file" ]]; then
  bash scripts/run_carla_day5.sh
  case_file="${MARSHAL_DAY5_OUTPUT:-/tmp/marshal-day5}/case.json"
fi
test -s "$case_file" || { echo "no assistance case at $case_file" >&2; exit 1; }

# The request the desk sent to the lab, as it sent it: the text and the case it came from.
field() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["test_requests"][-1][sys.argv[2]])' \
    "$case_file" "$1"
}
request="$(field text)"
source="$(field source)"
echo "incident: $source"
echo "request:  $request"

MARSHAL_LLM=gemini MARSHAL_LAB_REQUEST="$request" MARSHAL_LAB_SOURCE="$source" \
  MARSHAL_LAB_OUTPUT="$OUTPUT" bash scripts/run_carla_lab.sh

python3 - "$OUTPUT/manifest.json" "$source" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
# The loop is closed when the case is a reviewed test in the library. Whether the car passes it is
# the test's own result: a failure here is the lab doing its job, reported, not a broken chain.
ok = m["library_status"] == "in_library"
print(json.dumps({"type": "incident.done", "case": sys.argv[2], "scenario_id": m["scenario_id"],
                  "attempts": m["attempts"], "library_status": m["library_status"],
                  "passed_on_carla": m["judged"]["passed"], "failed_criteria": m["judged"]["failed_criteria"],
                  "result": "success" if ok else "not demonstrated"}, sort_keys=True))
sys.exit(0 if ok else 2)
PY
