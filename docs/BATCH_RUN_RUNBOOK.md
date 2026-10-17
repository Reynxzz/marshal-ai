# Batch run runbook

The batch API and runner must run on the CARLA host. The API launches
`scripts/run_carla_plant.sh` and, for incident batches, `scripts/run_carla_lab.sh`.
Only one batch can use the host's CARLA instance at a time; each batch accepts 1–6 cars.

Batch state lives in **Firestore** (`MARSHAL_BATCH_STORE=firestore`), not on the host's disk: the
EC2 root volume is deleted with the instance, so nothing durable may live there. The same
Workload Identity Federation credentials serve Firestore, GCS, BigQuery, Pub/Sub and Gemini — no
service-account key file on the host.

## One-time: GCP data plane + host identity

Apply the GCP stack (`marshal-tf/gcp/`, see its README), then write the host's credential file:

```bash
cd ~/marshal-app
mkdir -p ~/.config/marshal
python3 scripts/write_wif_credentials.py \
  --audience "$(terraform -chdir=~/marshal-tf/gcp output -raw wif_audience)" \
  --service-account "$(terraform -chdir=~/marshal-tf/gcp output -raw service_account_email)" \
  --output ~/.config/marshal/wif.json
```

## Host environment

In `~/marshal_batch_env.sh` (alongside the existing `marshal_env.sh` exports):

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/marshal/wif.json"
export GOOGLE_GENAI_USE_VERTEXAI=TRUE
export GOOGLE_CLOUD_PROJECT=<project>
export GOOGLE_CLOUD_LOCATION=asia-southeast2
export MARSHAL_BATCH=1
export MARSHAL_BATCH_STORE=firestore
export MARSHAL_BATCH_MAX_CARS=6
export MARSHAL_BATCH_TIMEOUT_S=1800
export MARSHAL_BATCH_LAB_TIMEOUT_S=900
export MARSHAL_BATCH_LAB_ADVERSARIAL=4
export MARSHAL_BUCKET=marshal-artifacts-<project>
```

No `alembic upgrade` is needed for batch runs; the SQL database only serves the demo/offline
mode (`MARSHAL_STORE=memory`).

Start the API in a terminal on the host:

```bash
source ~/marshal_batch_env.sh
.venv/bin/python -m uvicorn marshal_api.main:app --host 127.0.0.1 --port 8000
```

The runbook assumes CARLA 0.9.16 is listening on `localhost:2000`, Town05 is available, and the
host can stop/restart `carla-viewer` through the existing runner scripts. After any CARLA restart,
run `python3 scripts/ensure_carla_map.py --map Town05 --wait-seconds 600` — `/readyz` can be
green while the wrong map is loaded.

## Local dashboard tunnel

On the operator laptop:

```bash
ssh -i ~/.ssh/carla-key.pem -N -L 8000:127.0.0.1:8000 ubuntu@<carla-host>
```

Configure the frontend development API endpoint to `http://127.0.0.1:8000/api/v1`, then
start it with `bun run dev`. `VITE_MOCK_MODE` must be false for a live host run.

## Run and monitor

Submit a batch from the dashboard with 1–6 cars. The batch detail, seven flow counts, VIN
table, and event log poll every two seconds while running. Flow 6–7 run only if the plant
runner captures an assistance test request from the generated incident.

API checks:

```bash
curl -s http://127.0.0.1:8000/api/v1/readyz
curl -s 'http://127.0.0.1:8000/api/v1/batches?status=running'
curl -s http://127.0.0.1:8000/api/v1/batches/<batch-id>
curl -s 'http://127.0.0.1:8000/api/v1/batches/<batch-id>/events?since=0'
curl -s http://127.0.0.1:8000/api/v1/batches/<batch-id>/report
```

Cancel from the dashboard or call `POST /api/v1/batches/<batch-id>/cancel` with a
supervisor/engineer role. The service terminates the process group; allow up to ten seconds
for graceful shutdown before SIGKILL.

## Before the demo

- Verify `/readyz` (including `carla`), Town05, and that no other batch is active.
- Run a 1-car batch, then a 3-car batch and check its report labels and flow 6–7 result.
- Check that the viewer returns after each CARLA script exits.
- Keep the batch limit at six; parallel batches on one server are unsupported.
- If a batch is interrupted by an API restart, it is marked failed at startup and must be
  rerun. Check for orphaned `run_carla_plant.sh`, `demo_carla_plant.py`, or `run_carla_lab.sh`
  processes before retrying.
- Batch documents accumulate in Firestore; they are small (one doc per run plus its events).
  Delete old `batches/*` documents if a demo project needs a clean slate.

The live CARLA smoke checks and timeout/cancel checks are in `tasks/T10-tests-verification.md`.
