# Marshal

Gemini agents that run a car plant's yard, inspections and driving-system tests. Marshal never drives:
an existing driving system (CARLA's BehaviorAgent in the hackathon) does, and Marshal reaches it only
through a mission API. Design: [marshal_flow_design.md](marshal_flow_design.md).

## The seven flows

| # | Flow | Code | Gemini does | Code decides | Human decides |
| --- | --- | --- | --- | --- | --- |
| 1 | Yard traffic | `flows/yard.py` | Exception plans (late truck, closed track, full bay, line too fast) | Release, spacing, slots, shipping guard | Plans touching several cars or a zone |
| 2 | Remote assistance | `flows/assistance.py` | Reads the clip, ranks the car's options | Only offered options; wait only near people; 60 s escalation | Every option the car takes |
| 3 | Visual inspection | `flows/visual.py` | Defects per zone, build sheet comparison | Verdict from confidence (0.6 / 0.9) | Review cards |
| 4 | Functional check | `flows/functional.py` | Judges each track clip | Telemetry limits; pass only if both agree | — |
| 5 | Report and rework | `flows/report.py` | Report summary, rework plan | Bay list, safety order, failed-items-only re-inspection | Confirms rework or clears findings |
| 6 | Scenario generator | `lab/generator.py` | Text → scenario spec (never code) | Validator, 3-fix loop, fixed compiler, dry run | Engineer adds to library |
| 7 | Adversarial tester | `lab/adversarial.py` | Chooses variations, explains failures | Rule judge, shrinking, regression suite | Engineer approves releases |

`app.py` wires them through the seven topics. `lab/regression.py` is the release gate.

## Layout

```
src/marshal_ai/
  app.py          flows wired through the event topics; releases the lab can evaluate
  flows/          flows 1-5 (control tower)
  lab/            flows 6-7: spec, validator, compiler, simulator, judge, runners, generator,
                  adversarial search, regression suite and release gate
  llm.py          the one place Gemini is called (GeminiLLM, FakeLLM, RoutedLLM)
  offline.py      deterministic stand-ins for every Gemini task (MARSHAL_LLM=offline)
  driving/        mission API, rule checks, routines, fake and CARLA backends
  fakeworld.py    ground truth for simulated cars; fake gate and track cameras
  cameras.py      camera protocols; directory camera for real image sets
  plant/          plant map (named points, zones, routes) and plant state (trucks, bays)
  events/ store/  topics and buses; live records
  agents/         ADK: Plant Ops Coordinator + 7 specialists over the flows
  demo.py         demo plant for the five-minute script
apps/marshal_ops/ ADK app entry point
scripts/          demo.py, Pub/Sub setup, CARLA point picker
```

## Run it locally (no GPU, no GCP)

```
uv venv --python 3.10 && uv pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/python scripts/demo.py            # the five-minute demo, offline
.venv/bin/python scripts/demo.py --live     # Gemini for reasoning tasks (needs credentials)
```

To chat with the agents over the running fake plant, copy `.env.example` to `apps/marshal_ops/.env`,
set Gemini credentials, then `.venv/bin/adk web apps`.

## Remote CARLA 0.9.16

The CARLA-dependent process needs the compiled CARLA client and bundled
`PythonAPI/carla/agents` package. All runtime scripts are cloud-neutral: they expect CARLA at
`localhost:2000`, `/opt/carla`, Ubuntu/systemd, and Python 3.10. Cloud-specific commands are used
only to upload code and create SSH tunnels.

A non-mutating health check is available:

```
CARLA_HOST=localhost CARLA_PORT=2000 python3 scripts/check_carla.py
```

### Upload and prepare an AWS host

The generic uploader uses ordinary SSH and excludes Git, local virtual environments, caches and
artifacts:

```
MARSHAL_SSH_TARGET=ubuntu@AWS_PUBLIC_IP \
MARSHAL_SSH_KEY=~/.ssh/carla-key.pem \
  bash scripts/deploy_remote.sh

ssh -i ~/.ssh/carla-key.pem ubuntu@AWS_PUBLIC_IP \
  'cd ~/marshal-app && bash scripts/prepare_carla_host.sh'
```

`prepare_carla_host.sh` installs the pinned minimal mission runtime from CARLA's bundled cp310
wheel, waits for the API, loads Town05, verifies client/server 0.9.16, and installs the private
viewer. The AWS Terraform module emits both commands with the real address as
`marshal_upload_command` and `marshal_prepare_command`.

The AMI does **not** carry the Google SDKs, so anything using real Gemini or the GCP data layer
needs them installed into the same runtime directory first. A host rebuilt from the AMI fails with
`ModuleNotFoundError: No module named 'google'` until this is run:

```
sudo pip3 install --target /opt/marshal-runtime/vendor --upgrade \
  google-genai google-cloud-firestore google-cloud-storage google-cloud-bigquery google-cloud-pubsub
```

For the private GCP VM, upload still uses `gcloud compute scp --tunnel-through-iap`; after upload,
the same `prepare_carla_host.sh`, Day 1 and Day 2 commands apply unchanged.

The local fake backend remains the default. A macOS arm64/Python 3.13 machine can hold tunnels but
cannot load the bundled Linux client, so real BehaviorAgent and camera processes run beside CARLA
on the remote Ubuntu host. Pub/Sub/Firestore and Gemini remain optional and independent.

## Private browser viewer and next actions

The deployed viewer is deliberately small: one 640×360 RGB sensor at 2 FPS, JPEG quality 60,
bound to VM loopback only. Its systemd unit is capped at 30% of one CPU and 256 MiB RAM. Install or
refresh it after copying this repository to the VM:

```
cd ~/marshal-app
bash scripts/install_carla_viewer.sh
```

On the laptop, use the infrastructure module's private viewer tunnel output, leave it running, and
open the loopback URL:

```
# AWS: terraform -chdir=marshal-tf-aws output -raw carla_viewer_tunnel_command
# GCP: terraform -chdir=marshal-tf output -raw carla_viewer_tunnel_command
open http://localhost:8080
```

No public firewall rule is required. Health and still-image endpoints are available at `/healthz`
and `/snapshot.jpg`.

Day 1 map and mission readiness is now demonstrated on Town05. The minimal route uses spawn 155
and four 20 m legs on road 34, lane -2: `line_end → inspection_gate → test_track → report_point →
yard.Y1`. Install the pinned mission runtime and replay one blue vehicle through the existing
mission API:

```
cd ~/marshal-app
bash scripts/install_carla_mission_runtime.sh
PYTHONPATH="$PWD/src:/opt/marshal-runtime/vendor:/opt/carla/PythonAPI/carla" \
  python3 scripts/demo_carla_mission.py
```

The replay emits one status per simulated second, advances progress at each named point, emits
`mission.done` at `yard.Y1`, restores asynchronous world mode, and removes its vehicle. The browser
viewer is fixed over the start lane, so the vehicle is visible as it departs.

## Recording and replaying a run

The viewer cannot show a run: every `run_carla_*.sh` stops it for the run, and it is a fixed
camera over one road. Instead each run script records the run on the server (`MARSHAL_RECORD=0`
to skip), and a recording is filmed afterwards into an MP4. On the host:

```bash
sudo pip3 install --target /opt/marshal-runtime/vendor imageio-ffmpeg   # once: the encoder

bash scripts/run_carla_scene1.sh                    # records /var/lib/carla/recordings/scene1-<time>.log
bash scripts/run_carla_replay.sh                    # films the latest recording
bash scripts/run_carla_replay.sh yard-20261009-135434 --follow VIN-Y2 --speed 2
```

The video lands in `/tmp/marshal-replays/<recording>.mp4`; copy it off with `scp`. It opens on a
still of all the cars, then each drive is filmed by a still camera beside the road and the still
stretches are cut. Options: `--follow VIN`, `--speed N` (default 4), `--keep-idle`,
`--opening-s 0`, `--side left|right`, `--width/--height`.

Two things in CARLA 0.9.16 shape this, and both segfault the server: moving a camera during a
replay, and a camera west of the yard lane looking west. The replay therefore gives each shot its own
camera and films from the east side by default. See [PROGRESS.md](PROGRESS.md).

## Evaluating the flows

`python -m marshal_ai.eval` scores Gemini's part of flows 2, 3 and 4 against known answers, many
runs per case, through the flows' own code (fresh app, in-memory store, no CARLA). It needs only the
Vertex AI credentials.

```bash
python -m marshal_ai.eval eval/datasets/v1.json --runs 5
python -m marshal_ai.eval eval/datasets/v1.json --flows track --runs 10 --model gemini-2.5-pro
```

It prints the design's KPIs per flow (gate: first-pass yield, review share, false alarms,
build-sheet mismatches caught, latency against the 5 s budget; track: dark lamps confirmed as lit,
lit lamps rejected, unconfirmed; assistance: recommendation, people detected, high-risk
recommendations, objects boxed and how well the boxes overlap hand-labelled ones, time to the card
against the design's ~5 s) plus consistency across runs, and writes the full answers to `eval/results/`.

A dataset is JSON (`eval/datasets/v1.json`): each case names its frames and the right answer. Frames
point at a frozen copy in the artifacts bucket, because the run scripts overwrite evidence for the
same VIN; freeze new evidence with `scripts/snapshot_eval_media.py v3 gate/VIN-X/ ...`.

`eval/datasets/v2-damage.json` is the design's stand-in for scratches and dents: 30 photos of damaged
cars and 16 clean panels from Humans in the Loop's "Car parts and car damages" (CC0), run through the
gate's defect check one photo at a time (`--flows damage`). It scores damage found, the kind and the
zone it was put in, recall per kind, and false alarms on the clean panels.
`scripts/build_damage_dataset.py` rebuilds it from the downloaded archive: it uses the 441 photos
labelled with both parts and damage (the archive's two folder names are swapped), places each
damage on a part and the part in a gate zone, balances the kinds towards small damage, and crops
undamaged parts as the clean cases. Every clean crop was checked by eye; those rejected are listed
in the script with the reason. The set is mostly salvage-auction crash damage, far heavier than what
a finishing line sees, so read it as an upper bound on recall.

`eval/datasets/v3-report.json` scores flow 5 (`--flows report`): six failing cars as gate and track
records, no media, each with the bays the design's table gives, safety first. It scores the plan
that reaches the supervisor, Gemini's own plan before the router checks it (right bays, safety
bays first), and whether the summary names the safety defect before the cosmetic one. On
gemini-2.5-flash, 3 runs: every score 1.0, 2.8 s median.

`eval/datasets/v5-search.json` asks whether Gemini's steering earns its place in flow 7
(`--flows search`). Each case is a passing scenario; the search runs on the quick simulator against
`behavior-agent-1.0`, once with Gemini choosing each batch from the scores so far and five times with
no model, where the tester falls back to random variations: the same code with the steering
removed. On gemini-2.5-flash, 4 scenarios x 3 runs, 60-run budget:

| | Gemini | Random |
| --- | --- | --- |
| Runs to the first failure (median) | **11** | 19 |
| Failures per 100 runs | **2.9** | 2.0 |
| Scenarios with a failure found | 3 of 4 | 3 of 4 (one seed missed one) |
| Proposals skipped as unfair, per search | 0.5 | 1.6 |

By scenario: the design's example 3 vs 7 runs, a worker behind a parked car 16 vs 23.5, behind a
pallet stack 11 vs 31. Neither finds a failure in scene 3's incident in 60 runs: its worker stands
20 m beyond the box, out of reach of the parameters the search varies. Every run now keeps the reason
it was tried (`TestRun.reason`): the batch's reasoning, e.g. "speed near the person is consistently
at the 5 km/h limit, so the system is at its edge", or the shrinking step.

**The failure report** (`Scenario.failure_report`) follows the design's flow 7 example. The facts come
from the runs: the conditions, each measurement against its limit, and the single changes back to the
passing case that make it pass (found while shrinking). Gemini adds the likely cause and a concrete
fix, a speed cap for one zone and light, which is then tried in the simulator on both the failing and
the passing case. From the eval run above:

> SC-0300-v15 fails: at night, with the worker starting to move when the car is 8 m away at 1.2 m/s
> from behind a parked car, the car driving at 8 km/h, min gap to person 1.43 m (limit >= 1.5 m); speed
> near person 7.28 km/h (limit <= 5 km/h). It passes with the trigger distance back at 12 m or with
> light back at noon. Likely cause: ... reduced visibility at night and the worker appearing closer ...
> Fix tried in the simulator: with 5 km/h in test_track at night, the failing case passes and the
> passing case still passes. Clip: gs://marshal/lab/SC-0300-v15.mp4

On the 9 failures found, Gemini proposed a fix every time and every fix passed both checks
(`fix_proposed` 1.0, `fix_works` 1.0). Its free-text suggestion stays general ("brake earlier when a
pedestrian emerges"); the speed cap is the checkable part. On CARLA the check reads "not tried", as the
CARLA runner cannot change the driving system's limits.

**Its clean cases do not measure false alarms.** They are crops of used cars whose wear was never
labelled: on gemini-2.5-flash the gate flagged 7 of 16, and at full size at least three findings are
right (scratches on the blue door, scuffs on the grey bumper, a missing front number plate). Relabelling
them after seeing the model's answers would bias the set, so the false-alarm rate needs panels known
to be clean - new cars, such as the CARLA renders in v1, where it was 0.

### The gate's false alarms on clean cars (dataset v6-gate)

Qais's multi-car runs failed a clean car about one time in three, and v1 (one parked car, one light)
could not show it. `scripts/capture_gate_set.py` photographs clean sedans in five colours on the gate
under six suns, as the plant scene does (fixed gate cameras, loop running); with three of Qais's plant
cars that drove onto the mark, that is v6-gate: 33 clean cases and 10 with a wrong-colour build sheet.

The false findings all described the paint, not a mark: "a distinct pixelated or checkerboard pattern"
across both left doors, "cloud-like, irregular red patterns", "rough texture along the lower edge of
the door" - a low sun on glossy paint and the shading along the sills, reported at 0.6-0.9. Saying so
in the defect prompt removed some and made Qais's P1 worse (it then failed 2 of 2 for "paint scraped
off the lower door"), so it was reverted. Instead each view with a finding is checked against the
same camera's photo of the latest car that passed (`visual.verify`): a finding stays only if it is a
mark the passed car does not have, as flow 4 compares a lamp with a car that passed the step. The
first car of the day has no reference, so its findings stand and go to a person.

gemini-2.5-flash, 2 runs per case:

| | Before | With the reference check |
| --- | --- | --- |
| 30 clean cars: runs with a false finding | 6.7% | **0%** |
| 30 clean cars: passed first time | 0.93 | **0.97** |
| Qais's plant car P1 (sill shading) | flagged 1 run in 3 | passed 2 of 2 |
| Wrong colour caught | 10/10 | 10/10 |
| v2-damage, damaged photos caught (reference: another car's clean crop) | 93.3% | 93.3% |
| v1 gate | all correct | all correct |

The one clean case still not passing (a red car, sun 15 along the road) has its findings refuted
but goes to review because Gemini's all-clear for that view was below 0.9; that is left as it is, a
review rather than a pass on doubt. v2-damage-ref uses a different car as the reference, a harder
test than the gate's same-camera photo, and real damage survived it.

### A shift on the simulated plant: the operational KPIs

```
python -m marshal_ai.shift --cars 40 --seeds 10
python -m marshal_ai.shift --cars 40 --seeds 10 --damage-recall eval/results/v2-damage-20261009-234019.json
```

The eval datasets score Gemini's judgements. The design's other KPIs (cars per hour, line-to-yard
time, time to resolution, rework turnaround, cars shipped with an open finding) are about the
operation, so `marshal_ai.shift` runs one: cars leave the line every 2 min, each with a fault
drawn from a mix (dead headlight or brake lamp, wrong colour, missing badge, scratch, dent, panel
gap) or none; 8% stop at a box on the way to the yard, half with a worker; one truck runs 40 min
late. A simulated supervisor makes every human decision after 45 s, judging review cards by the
car's real state; bays repair in the design's minutes; a truck loads what is ready at its ETA and
waits up to 15 min only for passed cars on their way. Judgements come from the offline stand-ins,
which read the ground truth, so this measures the rules and hand-offs; `--damage-recall` drops the
gate's findings at the miss rate Gemini showed per kind on v2-damage, and the shift shows where
those misses go. Every car is checked against its real faults when it ships.

10 shifts of 40 cars (seeds 7-16):

| KPI | Perfect gate | Gate missing as on v2-damage |
| --- | --- | --- |
| Cars with a scratch, dent or panel gap | 68 | 68 |
| **Shipped with a real fault** | **0** | **16** |
| Shipped with an open finding (design target 0) | 0 | 0 |
| Gate first-pass yield / review share | 0.76 / 0.08 | 0.79 / 0.05 |
| Telemetry-camera disagreements / cars with a dead lamp | 47 / 47 | 47 / 47 |
| Rework rate | 0.37 | 0.33 |
| Rework turnaround, median | 43 min | 43 min |
| Line to yard, clean car, median | 14.7 min | 14.7 min |
| Assistance cases / with a worker / wait recommended | 34 / 13 / 13 | 34 / 13 / 13 |
| Time to resolution | 45 s (the supervisor's delay) | 45 s |
| Clean cars sent to rework | 0 | 0 |

What it found:

- **"Shipped with an open finding" cannot see a miss.** It reads the records, and a defect the gate
  never reported is not in them: with Gemini's measured recall (scratches 72%, dents 87%), 16 of 68
  damaged cars shipped and the KPI still read 0. Only checking against the car shows it, which on
  the real line means sampling shipped cars, or the design's human review for small findings.
- **A car that found the yard full was never shipped.** `send_to_slot` raised `yard_full` and left
  the car where its report finished; nothing tried again when a truck freed slots. The yard now
  re-tries every tick (`YardTraffic.waiting_for_slot`, tested). It also asked Gemini for a
  `yard_full` plan every time, about 22 calls in a 40-car shift that changed nothing; the design
  lists no such exception. Now the car simply waits, and a backlog of 3 or more cars waiting for a
  slot is the design's "line running faster than the yard", asked once per episode. Over the 10
  shifts, `yard.exception` calls went from 320 to 108 with every KPI unchanged.
- **The simulated plant was blind to lamp faults after the first car.** Once a passed car became
  the reference frame, the frame under test was rebuilt without its simulated ground truth, and the
  offline judge read the reference's. Every later dead lamp passed. Real CARLA runs were not
  affected (they send image bytes); the offline demo and tests were. Fixed and tested.
- **The 8-slot yard is the constraint.** Clean cars take 2.8 min from line to yard when a slot is
  free and 14.7 min in a full shift. Flow 1's numbers here depend on the truck schedule assumed (a
  truck is due 10 min after its last car), which the design does not give, so read them as how the
  yard behaves, not as the plant's figures.

## Real Gemini via Vertex AI

`GeminiLLM` uses `google-genai` with no explicit credentials, so it reads the environment. With a
service account that means Vertex AI mode:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=~/.config/marshal/sa-gemini-carla.json
export GOOGLE_GENAI_USE_VERTEXAI=TRUE
export GOOGLE_CLOUD_PROJECT=id-rd-ca-hanif-ali
export GOOGLE_CLOUD_LOCATION=us-central1
export MARSHAL_LLM=gemini          # "offline" uses the deterministic stand-ins
```

Keep the key outside the repository. `.gitignore` blocks `sa-*.json`, `*-sa.json` and
`service-account*.json`, but the safest place is `~/.config/marshal/` at mode `600`.

### Permissions actually required

| Need | Role | Status on `rey-gemini-sa` |
| --- | --- | --- |
| Gemini text + vision | `roles/aiplatform.user` | granted, verified |
| Pub/Sub bus (`MARSHAL_BUS=gcp`) | `roles/pubsub.admin` (or `editor` + `publisher` + `subscriber`) | granted, verified |
| Firestore store (`MARSHAL_STORE=gcp`) | `roles/datastore.user` | **missing** |

APIs `aiplatform`, `pubsub`, `firestore` and `datastore` are all enabled on the project.

Two gotchas seen in practice. A freshly granted role can take a few minutes to propagate — Pub/Sub
returned `IAM_PERMISSION_DENIED` at first and worked shortly after with no change. And Firestore
needs a **database to exist**, which is separate from enabling the API and from IAM:

```
NotFound 404 The database (default) does not exist for project id-rd-ca-hanif-ali
```

Create it once (the location is permanent), then grant `roles/datastore.user`:

```bash
gcloud firestore databases create --location=asia-southeast2 --project=id-rd-ca-hanif-ali
gcloud projects add-iam-policy-binding id-rd-ca-hanif-ali \
  --member=serviceAccount:rey-gemini-sa@id-rd-ca-hanif-ali.iam.gserviceaccount.com \
  --role=roles/datastore.user
```

No Cloud Storage or BigQuery role is needed yet: `gs://` URIs are recorded as strings and nothing
in `src/` imports those clients.

## Evidence storage (Cloud Storage)

Every record already carried a `gs://` URI — a gate view, a track frame, the forward view at an
assist stop — but nothing was uploaded, so the design's promise that "every verdict can be traced
back to a photo" did not hold and those URIs pointed at nothing.

```bash
export MARSHAL_ARTIFACTS=gcs            # "none" (default) keeps the URI as a label
# export MARSHAL_BUCKET=...             # defaults to marshal-artifacts-$GOOGLE_CLOUD_PROJECT
```

Bucket `marshal-artifacts-id-rd-ca-hanif-ali` exists in `asia-southeast2`, with uniform
bucket-level access and **public access prevention enforced** — these are internal plant photos.
`run_carla_day4.sh` and `run_carla_day5.sh` default to `gcs`.

Records use a `gs://marshal/...` convention that predates the bucket, and bucket names are globally
unique, so the uploader keeps the object path and rewrites the bucket. Uploads are best-effort by
design: a storage outage logs a warning and the URI is still recorded, because a failed upload must
never change a verdict.

Verified on the host after day 4 and day 5:

```
gs://marshal-artifacts-…/assist/AC-VIN-STUCK-1.jpg        567296 bytes  image/jpeg
gs://marshal-artifacts-…/track/VIN-OK/step1_headlights.jpg   481280 bytes
gs://marshal-artifacts-…/track/VIN-DARK/step1_headlights.jpg 489472 bytes
```

Downloading the two track frames shows exactly what the verdicts claim: `VIN-OK` casts light pools
on the road, `VIN-DARK` casts none. That is the audit trail — a supervisor can pull the frame behind
any `camera_ok` decision.

No BigQuery yet: nothing in `src/` imports that client, so record history is still the live store.

## Record history (BigQuery)

The live store keeps one current copy per record. History makes a verdict auditable after the car
has shipped.

```bash
export MARSHAL_HISTORY=bq               # "none" (default) keeps only the live copy
```

Dataset `marshal`, table `record_history` in `asia-southeast2`, partitioned on `recorded_at` and
clustered on `kind, key` so a shift or VIN query stays cheap. `run_carla_day4.sh` and
`run_carla_day5.sh` default to `bq`.

Two deliberate choices, both worth knowing:

- **It appends on every `put`, by wrapping the store.** `store/__init__.py` says history is written
  "by the pipelines that produce them, not through this store", but nothing did, and a car's record
  is updated many times — released, inspected, reported, reworked, re-inspected. Per-pipeline calls
  only capture the moments someone remembered to add one, so most of the trail would be lost.
- **One table holds every kind**, rather than six mirrors of the pydantic models that would drift
  from them. `kind` and `key` cover VIN lookups, `recorded_at` covers shifts, and `payload` is
  queryable with JSON functions. The column is not called `at`, because that is a reserved keyword
  in BigQuery SQL and every query would need backticks.

Appends are best-effort, like artifact uploads, and retried briefly: a freshly created table answers
404 to streaming inserts for a few seconds. Losing history must never change a verdict.

Verified end to end — four state changes written and read back in order:

```
Car        state=line_end
Car        state=inspection_gate
Car        state=test_track
CarReport  verdict=FAIL
```

```sql
SELECT recorded_at, kind, JSON_VALUE(payload, '$.verdict') AS verdict
FROM `id-rd-ca-hanif-ali.marshal.record_history`
WHERE key = 'VIN-DARK' ORDER BY recorded_at
```

## Test lab on real CARLA (day 3 checkpoint)


The day-3 checkpoint from `marshal_flow_design.md` — *one generated scenario must run in CARLA and
be judged pass or fail automatically* — runs end to end on the GPU host:

```
cd ~/marshal-app
bash scripts/run_carla_lab.sh                          # flow 6 only
MARSHAL_LAB_ADVERSARIAL=10 bash scripts/run_carla_lab.sh   # plus flow 7
MARSHAL_LAB_REQUEST="..." bash scripts/run_carla_lab.sh    # your own situation
```

The wrapper checks CARLA, loads Town05, stops the viewer for the run (the compiled scenario takes
the world into synchronous mode) and restores it afterwards. `scripts/demo_carla_lab.py` drives the
pipeline: request → spec → validate → compile → run in CARLA → rule judge → engineer review →
library. Artifacts land in `/tmp/marshal-lab`: `<id>.spec.json`, the compiled `<id>.scenario.py`,
the judged `<id>.run.json`, `<id>.search.json` and `manifest.json`.

`MARSHAL_LLM` defaults to `offline` here, so the deterministic stand-ins write the spec and choose
variations; the CARLA execution and the rule judge are real either way. Set `MARSHAL_LLM=gemini`
with credentials to use the model.

**Keep `--batch 1` on a single CARLA server.** Every compiled run puts the world into synchronous
mode and ticks it, so concurrent runs corrupt each other and the first one to finish restores
asynchronous mode under the others. The design's 8-way parallelism needs one server per slot.

### Demo scenes 4 and 5 on CARLA (`scripts/run_carla_release.sh`)

```
bash scripts/run_carla_release.sh                    # MARSHAL_LAB_ADVERSARIAL=16, behavior-agent-1.0
```

One process on the host, Gemini throughout, BigQuery history on: the design's example scenario is
generated, judged on CARLA and put in the library; the search varies it on CARLA until a variant
fails, shrinks it and adds it to the library as a regression test with its failure report; then the
release gate runs every library test against the driving system. Each `ReleaseReport` is stored
(collection `releases`) and so becomes a history row; "what changed" is computed against the most
recent other release, from the store or, when this process never saw one, from BigQuery
(`History.latest`). Run on 2026-10-10:

```
SC-0142-v1 fails: at dusk, with the worker starting to move when the car is 9 m away at 1.3 m/s from
behind a forklift, the car driving at 9 km/h, speed near person 9.55 km/h (limit <= 5 km/h). It
passes with the car's speed back at 8 km/h. ... The fix was not tried: this runner cannot change the
driving system.
Release behavior-agent-1.0: 2 tests, 1 passed, 1 failed. 1 failing test, release blocked.
```

The search found the failure in 2 runs. A fresh process read the report back from BigQuery. It had
no earlier release to compare with: the only release CARLA can run is 1.0 (`CarlaRunner.with_driver`
returns the same BehaviorAgent), so "now passing since 1.0" for the fixed 1.1 is shown on the
quick simulator (`scripts/demo.py`, tests), not on CARLA.

### Map points the lab needs

Lab scenarios drive `line_end → finishing`, so the finishing branch is pinned to real Town05
waypoints on road 34, lane -2, walking forward from `report_point`:

| Point | CARLA transform | Note |
| --- | --- | --- |
| `finishing_corner.p1` | 204.93, −114.62, yaw −98.9 | curve begins |
| `finishing_corner.p2` | 199.64, −134.95, yaw −110.3 | blind corner |
| `finishing_corner.p3` | 190.45, −153.83, yaw −121.6 | occluder sits here |
| `finishing` | 177.72, −170.54, yaw −133.0 | route end |
| `lane_c` | 203.01, −73.89, yaw −90.7 | adjacent lane at `report_point` |

10 of 25 plant points now carry transforms. The rework bays, buffer zone, `yard.entry` and
`yard.Y2`–`Y8` are still `carla: null`, so scenarios routed there fail the dry run with a clear
"no CARLA transform" error. Use `scripts/pick_carla_points.py` to pin more.

Day 2 is packaged as a provider-neutral serial workflow:

```
cd ~/marshal-app
bash scripts/run_carla_day2.sh
```

It stops the optional viewer, loads Town05, captures each gate view in a fixed-camera process,
health-checks/restarts CARLA between phases only when necessary, runs R-12 lighting steps 1-4 with
one pre-created track camera, writes `/tmp/marshal-day2/manifest.json`, and restores the viewer.
This lifecycle is conservative enough for GCP and remains appropriate on AWS.

AWS g4dn.xlarge proved the complete workflow end to end: four distinct 640×360 gate views, four
unique track images, telemetry checks true, active CARLA bits `LowBeam=2`, `LeftBlinker=32`,
`Brake=8`, `Reverse=64`, 54 status records, `mission.done` at `report_point`, clean actor removal,
and automatic viewer restoration. The right body side uses the proven left-side camera pose with the
stationary vehicle rotated 180°, because Town05's east-side camera pose produces no stream on both
clouds.

GCP proved the same track phase but its 7.5 GiB host needed emergency swap and CARLA signal-11
recovery around camera cleanup. Keep the serial/process-isolated workflow on both providers until a
larger AWS scenario is measured.

Offline reasoning cannot judge real pixels because it intentionally reads only fake-camera truth.
The manifest deterministically verifies JPEG presence, telemetry and CARLA light state; real image
judgement is Gemini's, which is now wired up along with Pub/Sub, Firestore, Cloud Storage and
BigQuery. What is still not implemented is the physical reverse, brake and full-lock manoeuvres —
the car is held stationary for the length of a routine step, so those rest on telemetry.

Do not send a CARLA mission to any named point whose `carla` transform is still null.

## Status

**Where this stands.** All seven flows run against a real CARLA 0.9.16 server and real Gemini via
Vertex AI, with records in Firestore, evidence in Cloud Storage and history in BigQuery. Demo scene 1
runs end to end and repeatably. 213 tests pass. The latest checkpoint, with what changed and
what is still broken, is in [PROGRESS.md](PROGRESS.md). The three sections that matter most for reading the
current state are **All seven flows on the real server**, **Demo scene 1**, and **Still open against
the design** — the rest of this section is the trail of what was tried and what turned out to be
wrong, kept because several of the wrong turns are easy to repeat.

Verified locally: all seven flows on the fake plant with offline stand-ins, plus CARLA connection,
viewer, spawn-synchronization, calibration, and mission-demo tests.

Verified on GCP: IAP transport; matching CARLA 0.9.16 client/server; Town05; private browser
viewer; connected route planning; two complete BehaviorAgent Day 1 missions; three distinct gate
perspectives; and the complete R-12 lighting track phase with artifacts and deterministic telemetry/
light-bit evidence. Emergency swap prevented further host OOM, but CARLA camera actor cleanup still
produced signal 11 on the undersized host.

Verified on AWS g4dn.xlarge: full NVIDIA 580 server driver, Vulkan, CARLA 0.9.16, Town05, private
viewer, provider-neutral upload/preparation, Day 1 with 29 status events and `mission.done`, and the
complete Day 2 manifest. Post-run state had zero vehicles, one viewer sensor, asynchronous mode,
11 GiB available RAM, no swap, no OOM, and healthy EC2 system/instance checks.

**Day 3 checkpoint met on AWS.** Flows 6 and 7 run against the real CARLA server:

- Flow 6: `SC-0142` generated from plain language in 1 attempt, validated, compiled, run in CARLA,
  and judged automatically — `passed: true`, worker trigger fired at t=66.4 s, `min_gap_to_person_m`
  2.28 against a 1.5 m limit, `no_contact` and `reached_goal` both 1.0, route completed in 79.5
  simulated seconds. Engineer review moved it to `in_library`.
- Flow 7: the search found a real BehaviorAgent failure in 4 runs and shrank it to a single
  parameter. `SC-0142-v3` (`trigger_distance_m` 9 → 7) collides with the worker:
  `no_contact` 0.0, `min_gap_to_person_m` 0.0, `speed_near_person_kmh` 7.89 against a 5 limit.
  Night and pallet-stack variants still pass, so trigger distance is the edge. Added to the
  regression suite as `SC-0142-v3`. Reproduced across two runs.

Four compiler defects were fixed to get there, all of which only appear against real CARLA:

1. `BehaviorAgent` was constructed before the world ticked, so it planned from the origin `(0,0,0)`
   and drove straight into a guardrail — 3,532 collision events and zero progress.
2. Actors leaked by a timed-out run blocked the same spawn point for every later run; lab actors are
   now tagged `role_name=marshal-lab` and cleared at startup.
3. A worker placed `behind:` an occluder spawned inside that occluder's bounding box; placement now
   clears the occluder's half-length and retries widening offsets.
4. The run budget was 120 simulated seconds while the `line_end → finishing` route alone needs 76;
   it is now 200.

**Day 4 complete on real CARLA with real Gemini** (`scripts/run_carla_day4.sh`). Demo scene 2 runs
end to end: a healthy car passes, and a car whose headlight bit is held off while its telemetry
reports healthy fails on the disagreement, is reported, and is driven to its bay.

```
VIN-OK    telemetry_ok=true  camera_ok=true   PASS   at report_point
VIN-DARK  telemetry_ok=true  camera_ok=false  FAIL   at rework.electrical
          bays=[rework.electrical]  failed_checks=[headlights]
          summary (Gemini): "passed visual inspection but failed functional checks.
                             The headlights are not working correctly."
```

Getting flow 4's camera half to work at all took four fixes, all invisible without real frames:
telemetry was being shown to the camera judge (it confirmed a dark lamp at confidence 1.0);
captures reused an in-flight frame of the *previous* car; 640×360 was too small to tell a lit lamp
from a dark one; and absolute lamp judgement fails outright — both `gemini-2.5-flash` and
`gemini-2.5-pro` insisted a dark headlight was lit until given a reference frame from a car that
passed the same step, after which both answered correctly at 0.99.

Flow 5 needed three more: `merge_verdict` returns REVIEW when either result is missing, so a car
with no failed checks reached the rework path and crashed on `tickets[0]`; proposals were read
before the router's own Gemini call had written the report; and confirming a rework after the car
was cleaned up failed with `KeyError`, because dispatch drives the car to the bay.

**The rework loop closes on CARLA** (`MARSHAL_DAY4_REWORK=1 bash scripts/run_carla_day4.sh`). After
the car reaches `rework.electrical`, the bay reports the repair done; the CARLA backend's `repair`
relights the lamp (the electrical bay clears the car's fault, as the fake world's repair does), the
router sends the car back for the failed step only, and the report turns to PASS. Twice in a row:

```
VIN-DARK  headlights  telemetry_ok=true  camera_ok=false  FAIL  -> rework.electrical (confirmed, arrived)
          repair_done -> faults_left=[] -> step 1 re-run only -> camera_ok=true -> report PASS (20 s)
          summary (Gemini): "The car failed its functional inspection because the headlights were not
                             operating correctly. ... Re-inspection passed."
```

The bay used to come from an exact lookup on the finding's type, but Gemini writes free text: in
the evaluation answers 13% of findings ("scratches", "scuff", "paint_damage", "chip", "corrosion",
"headlight_haze") missed the table and went to the body bay. `bay_for` now reads the words against
the design's table, lights before paint.

**Incident → test on CARLA** (`scripts/run_incident_to_test.sh`): the design's loop between
operations and the test lab. Scene 3 runs, the desk's case goes to the lab, Gemini writes a spec
from the case, and it is validated, checked fair, dry-run, run and judged on CARLA and reviewed into
the library tagged `from-incident`. `MARSHAL_INCIDENT_CASE=/tmp/marshal-day5/case.json` reuses a case.

```
AC-VIN-STUCK-1  "A cardboard box is in the lane 2.3m ahead, with a person walking further ahead..."
SC-0142         route inspection_gate->test_track->report_point at 8 km/h; box at test_track,
                worker walking at report_point; no_contact, min_gap_to_person_m >= 1.5,
                speed_near_person_kmh <= 5        generated first try, in_library
CARLA run       FAIL no_contact: "contact with static.prop.box01"; worker kept 20 m away (twice)
```

The test fails, and that is the finding: BehaviorAgent brakes for vehicles and walkers, not props,
so the stand-in drives into the box. Scene 3's "2.3 m ahead" is measured from the car's centre,
about half a car length, so its stop was most likely this contact too.

It took three fixes. Generation failed on all three tries at scene 3's case (4 attempts, ~2 min, then
rejected): Gemini placed the box and worker at `test_track`, where the car spawns, and, told only
that "no_contact is not achievable", loosened the criteria instead of moving them. The validator now
rejects an actor at the car's start and says how to fix it; the prompt says where the car starts,
that only a forklift, pallet stack or parked car can hide a person (first tries wrote `behind:box`
for "a person walking behind it"), and to use only the example's fields; and a request from an
assistance case is shown an incident example beside the supervisor one. `run_carla_lab.sh` takes
`MARSHAL_LAB_SOURCE`, so the case id reaches the scenario. Dataset `v4-scenario` (`--flows scenario`)
scores it on the quick simulator: 4 requests x 3 runs, a runnable test 12/12 (first try 9/12),
faithful to the request (zone, actors, where they stand, criteria, time of day) 100%, 13 s median.

**Retrieval library for generation** (`lab/library.py`), the design's ChatScene method: a new request
is shown the two reviewed tests most like it, as request-and-spec pairs, instead of the same design
example every time. The library is five hand-checked seeds (the design example, scene 3's box, a car
braking ahead, a forklift crossing, a worker crossing at night in rain) plus every scenario an
engineer puts in the library; regression variants are left out. Ranking is by shared words, weighted
by rarity, after folding plant synonyms (pedestrian -> worker, evening -> dusk); a request from the
assistance desk always gets an incident example. Each seed passes the generator's own validation,
fairness and dry-run checks (`tests/test_flow6_library.py`). `generator.retrieval = False` shows the
old fixed examples, and v4-scenario, now 8 requests, runs every case both ways. gemini-2.5-flash,
3 runs each:

| | Retrieval | Fixed examples |
| --- | --- | --- |
| Runnable test | 24/24 | 24/24 |
| Right on the first try | **18/24** | 15/24 |
| Attempts, mean | **1.33** | 1.58 |
| Faithful to the request | 100% | 100% |

The gain is small and from two requests (a forklift crossing at the corner 3/3 vs 1/3, a box with
nobody near 1/3 vs 0/3); the run before tied at 18/24, so read it as no worse and probably better.
Building it fixed two things that helped both: the dry run no longer asks a car behind a braking car
to reach its goal (the car-braking request went from rejected every time to generated every time),
and each fix attempt now sees all earlier errors, because shown only the latest, Gemini removed
`reached_goal`, added an unmeetable speed limit, then put `reached_goal` back. Each attempt's errors
are kept (`GenerationResult.retried_for`).

**Day 5 complete on real CARLA with real Gemini** (`scripts/run_carla_day5.sh`). Demo scene 3 runs
end to end. `carla_backend` used to note that raising `assist.request` was unwired, so flow 2 only
ever worked against the fake plant; the CARLA backend now raises it itself when the agent stalls
mid-mission with something in front of it. A stall with nothing ahead is ignored, because
BehaviorAgent also stops for its own reasons.

```
request  "unknown object in lane: an object (static.prop.box01) 2.3 m ahead"
clip     519168 bytes of real forward view
Gemini   "An object is 2.3 m ahead in the lane, and a person is walking on the
          right side of the car."        people_or_moving_equipment=true
ranking  wait_for_removal low / pass_left_3kmh medium / reroute_lane_c medium
card     recommended wait_for_removal, confidence 1.0
human    supervisor-ana chose it, the car was released, case sent to the test lab
```

The saved frame confirms the description: the box sits in front of the bonnet and the worker stands
to the right. The rule that a person in the scene allows only a wait recommendation held without the
code needing to override Gemini. The request carries real bytes because a `gs://` URI that does not
exist cannot be read by Vertex AI — the same 403 that made flow 3 unusable on the fake plant.

**Flow 1 complete on real CARLA** (`scripts/run_carla_yard.sh`). Cars queue at the line end and are
released under the controller's own rules; a truck delayed 40 minutes goes to Gemini, which proposes
a re-plan, and because it moves several cars it waits for a supervisor before anything is sent.

Running it against a real server showed the release spacing rule had never fired there, for two
reasons the fake backend hides:

- It compared `Status.x/y` against `rt.map.points["line_end"]`. The plant map holds abstract
  coordinates (line_end is 0,0) while a real backend reports world coordinates (207.27,-13.93), so
  for a car standing still at the line end it computed 207.7 m, always read "clear", and released a
  second car 16 m behind the first. The fake backend reports plant coordinates, so both sides agreed
  there and every test passed. The backend now owns its frame (`reference_xy`).
- It only had a position once a `Status` had arrived, and otherwise fell back to wall-clock time. On
  a loaded CARLA server simulated time runs slower than real time: 18 real seconds passed while the
  car had moved 16 m. The rule now asks the driving system directly (`position_xy`), then the last
  `Status`, then time.

Geometry worth knowing: `inspection_gate` is 20 m from `line_end` on Town05 while `MIN_SPACING_M` is
25, so a car can never be 25 m out while still in the first segment — leaving it by reaching the gate
is the normal way it clears, and BehaviorAgent finishes within a few metres of its target, so that
happens at about 16 m.

All 25 plant points now carry CARLA transforms; an approved re-plan had failed on
`yard.Y7 has no CARLA transform`.

### Why flows cannot all share one process

Four 1280×720 gate cameras streaming at once filled the server log with
`Invalid session: no stream available`. The fix was to stop pointing four cameras at the gate — it
now moves a single camera between poses, the pattern Day 2 has always used — and to stop a capture
ticking the world when the driving loop is already ticking it. Flow 1 runs with **zero CARLA
restarts** where it previously restarted the server several times.

### Correction: unsubscribing idle cameras was the wrong fix

This section previously said cameras stream *only while a frame is wanted*. That was reasoning about
server load, and it broke two flows:

- While the driving loop is running it is the only ticker. A camera that subscribes in the middle of
  a capture has no tick of its own to wait for, so the track phase failed with `camera produced no
  frame after bounded warm-up`.
- Cycling the subscription also wedged the gate: `attempting to unsubscribe from stream but sensor
  wasn't listening`, then a destroy that timed out and a segfaulted server.

Cameras are now created once and stay subscribed. A capture taken while the loop is running takes
the **freshest frame already delivered** rather than waiting for a new one — what Day 2's
`save_latest` does, and why Day 2 never hung. The cost is stated plainly: that frame is up to a few
ticks old, so it cannot distinguish a lamp that has just changed from one that has not. A step that
needs to show a change over time uses a clip instead — see **Clips** below.

A gate inspection still cannot share a process with a running driving loop:

- **Capture inline**, blocking the loop: the loop is the only thing ticking the world, so no frame
  ever arrives and the capture times out.
- **Capture concurrently** with the loop: the server segfaults —
  `Waited for 10 seconds on IO....Signal 11 caught.`

So each flow gets its own process. `--with-gate` is off in the yard demo, and demo scene 1 runs as
four processes.

## All seven flows on the real server, after the camera changes

The camera and verdict changes above touch every flow, so each was re-run against real CARLA and
real Gemini rather than assumed:

| Flow | Script | Result |
| --- | --- | --- |
| 1 yard | `run_carla_yard.sh` | `success` — rules held on every release; truck delay → Gemini proposal → supervisor approval → 3 cars re-slotted |
| 2 assistance | `run_carla_day5.sh` | `success` — `people_or_moving_equipment: true`, recommends `wait_for_removal` |
| 3 visual | `run_carla_scene1.sh`, `run_carla_day2.sh` | `PASS` on a matching car; 4 gate views ~150 KB each |
| 4 functional | `run_carla_day4.sh` | healthy `PASS`, dark lamp `FAIL` on the telemetry/camera disagreement → `rework.electrical` |
| 5 report | `run_carla_scene1.sh` | `PASS` merged from both results → car parked in `yard.Y1` |
| 5 rework | `MARSHAL_DAY4_REWORK=1 run_carla_day4.sh` | `success` — dark lamp → electrical bay → repaired → failed step only re-run → report `PASS` |
| 6 generate | `run_carla_lab.sh` | `SC-0142` generated in 1 attempt, dry run clean, judged `passed: true`, moved to `in_library` |
| 7 adversarial | `run_carla_lab.sh` | search shrinks the failure to one parameter (`trigger_distance_m` 9 → 7) |

Flow 2 was broken by the camera work and is now fixed properly: the assistance frame comes from a
camera **on the car**, created with it and streaming for as long as it exists. It used to be taken by
spawning a camera behind the car at the moment it stopped, which cannot work — the stall is detected
inside the driving loop, so the loop was blocked waiting for a frame only it could produce. That
failure was swallowed, the request went out carrying a `gs://` name nothing had been uploaded to, and
it surfaced as a **403 from Vertex AI** trying to read it. Media with no bytes is no longer presented
as a clip.

## Flow 4 on the whole R-12 routine, and what this vehicle actually shows

Running all six steps against real CARLA failed three of them on a healthy car. None of the failures
were the judgement's fault. Each was the routine asking for something the simulation or the vehicle
asset does not do, and the checks were right to say so:

| Step | Camera | What the asset does |
| --- | --- | --- |
| 1 headlights | confirmed | both lamps light |
| 2 indicators | **telemetry only** | `RightBlinker` lights one amber segment; `LeftBlinker` renders nothing at all - the frame is identical to lights-off |
| 3 brake_lights | confirmed | one continuous amber bar, with no separate third lamp |
| 4 reverse | confirmed | the reverse lamp lights; the car does not roll back |
| 5 brake_test | **telemetry only** | the car is held stationary, so nothing happens |
| 6 full_lock | **telemetry only** | likewise |

Three expectations described a real car rather than this one and were corrected: brake lights asked
for "left, right and the third (centre high) lamp" on a car with a single bar, and reverse asked to
see 3 m of movement that never happens. The indicators step was checked by setting each light state
on its own and photographing the result - one side of this asset is simply not drawn - so it rests on
telemetry, for the same reason horn and wipers are out of scope.

The checklist now distinguishes three kinds of step: one a frame settles, one needing the run of
frames the design calls a clip, and one the simulation does not perform. The verdict has three
outcomes to match: a camera that **contradicts** telemetry is a FAIL, a camera that could have
settled a step and did not is a REVIEW, and a step with no camera to speak for it passes on telemetry
while the record says so. A report that says PASS can now say how much of it two sources agreed on.

The full routine now returns **PASS** with headlights, brake lights and reverse camera-confirmed.

## Clips (`_FrameBuffer`)

The design asks for a 3 s clip of each track step and a 10 s clip before a stop; both had been one
still. A clip cannot be taken at the moment it is wanted, because that moment is inside the driving
loop - the loop is blocked in the very call asking for frames, so none can arrive. The frames have to
be there already, and they were, since the cameras stream continuously. Each camera now keeps its
recent past.

Three things had to be got right, each found by looking at the frames that came back:

- **Memory.** A frame at 1280x720 is raw BGRA, about 3.7 MB, and the server makes 20 a second, so ten
  seconds would be some 700 MB per camera. Kept frames are sampled across the window and encoded to
  JPEG as they arrive, which is also safer: a frame object belongs to its sensor's stream, and holding
  a few of them for later encoding took the server down with a 20 s timeout.
- **Evidence has to follow the car.** Track cameras fixed to the map lose it - the car moves while it
  performs the routine, and by the end of a clip it was a speck twenty metres away with no lamp on it
  resolvable. The cameras are now attached to the car, one at each end.
- **Framing.** At the default 90 degrees the car sat in the middle of a wide night scene and a lamp
  was a handful of pixels. Narrowed to 38 degrees the car's end fills the frame, which is what made
  the difference between a guess and a reading - it is also what revealed that two of the expectations
  described a different car.

Flow 2 now sends the seconds before a stop as seven frames, and still recommends `wait_for_removal`
with a worker beside the box.

## Still open against the design

| Design says | Status |
| --- | --- |
| 3 s clip per track step (flow 4) | Built, as a sampled run of frames from the camera's recent past. The steps that remain unconfirmed are limited by the asset and the simulation, not by the evidence. |
| 10 s clip with the assistance request (flow 2) | Built: seven frames of the approach to the stop. Not an encoded video - a video file would need an encoder on the host and buys nothing for the judgement. |
| 60 runs per scenario, 8 in parallel (flow 7) | **Serial only.** Parallel runs need one CARLA server per slot; the search works but takes the full budget serially. |
| Release gate blocks a release on a failing test | Built and run on CARLA (`run_carla_release.sh`): the failure the search found joins the suite and the gate reports "1 failing test, release blocked"; reports go to BigQuery history and the previous release is read back from it. The fixed release 1.1 runs only on the quick simulator. |
| Demo scene 2 uses a dead brake light | Built with a dead **headlight**: it is the fault the camera reads most clearly, and the disagreement is the point. |
| Scratches and dents from a car-damage image set | Measured, not part of a live run: dataset v2-damage (see "Evaluating the flows"). gemini-2.5-flash finds 97% of damage, in the right zone every time; subtle kinds (paint, broken, missing parts) 21-40%. |
| Assistance card with the object boxed (flow 2) | Built. A second call, `assist.locate`, runs beside the assessment with thinking off and boxes the object and every person near it ([ymin, xmin, ymax, xmax], 0-1000, on the frame it names, which is uploaded). On the scene 3 frame the worker's box overlaps the hand-labelled one 0.78; the cardboard box's is on the box but twice its width (0.25). Asked inside the assessment, both boxes landed beside the objects (0.17). A located person counts as "people near" even if the assessment missed them. |
| Card in about 5 s (flow 2) | **4.3 s median, 19 of 22 cards under 5 s** on dataset v7-assist (`scripts/capture_assist_set.py`: a box with workers beside, beyond, on the shoulder, two workers, none, a low sun; plus scene 3), from 13 s. The assessment no longer thinks (`assist.assess` in `TASK_THINKING_BUDGET`): same recommendations, a wait whenever a person was in view, no high-risk option. Without thinking its situation line first left the people out (2 of 16), so the field now asks for every person in view and where (16 of 16, against 12 with thinking on). |
| Gate decides in under 5 s (flow 3) | **Median 5.3 s, 90% within 7.3 s** on v6-gate's 1280x720 frames (4.6 s on v1's smaller ones), from 12 s. The gate waits for the slowest of its five calls, and flash sometimes thought for 10-20 s on one view: its three tasks now think at most 512 tokens (`llm.GATE_THINKING`), same answers, damage found on v2-damage unchanged (scratches 80%). A gate call still out after 10 s is sent again and the first answer used, for stalls (one car took 70 s with nothing to think about). Measured and rejected: thinking off (first-pass yield 0.31), low image resolution (scratches 75% -> 60%), and re-sending at 4 s (most calls are still out at 4 s). Under 5 s for every car needs a faster model call than flash gives on these frames. |

## Demo scene 1 on real CARLA (`scripts/run_carla_scene1.sh`)

A car leaves the line under the spacing rule, is inspected at the gate, drives the lighting routine,
and gets a clean report — four phases, one flow each, against the real server and real Gemini:

```
release  VIN-S1 released, rule "within 25 m of line end" held, arrives at inspection_gate
gate     PASS   4 views captured (~150 KB each), 0 findings, 0 mismatches
track    PASS   headlights: telemetry_ok=true camera_ok=true (504 KB frame, taken while driving)
report   PASS   "All visual and functional checks passed."  -> car parked in yard.Y1
```

Zero CARLA restarts, and repeatable: two consecutive runs gave the same three PASS verdicts and the
same slot. Getting there uncovered seven faults, none of them cosmetic:

1. **Gate arrival was never recorded.** It was written by the visual inspector when the inspection
   finished, so a car standing at the gate still reported `location: line_end`, and anything waiting
   on the arrival rather than the verdict waited forever. An earlier claim that this phase worked was
   wrong: what had been checked was `state`, which is set at release, not on arrival.
2. **A prewarmed camera was silent.** `prewarm_camera` spawned without subscribing while `capture`
   spawned and subscribed, because each built the sensor itself. There is now one creation path.
3. **Capture waited for a frame the blocked loop had to produce.** See the correction above.
4. **`set_weather` is ignored straight after a map load.** The first version asked once and reported
   the value it had asked for, so it looked correct while evidence frames came back in broad daylight
   and a healthy car failed its headlight check. The value is now read back and the request repeated.
5. **Phases trusted stale records.** Each phase waits for its own result, which a result left by an
   earlier run satisfied instantly — the track phase once reported a FAIL whose evidence frame came
   from the previous run, having taken no frame at all. A phase now deletes what it is about to
   produce.
6. **A phase run on its own failed with "unknown VIN".** Each phase can be started alone, and the
   phases before it are what normally put the car record in the store. Run against a cleared store,
   the track phase was quietly depending on a record it had not made. Every phase now creates the
   record it needs and leaves an existing one alone, so a full run still carries the verdicts already
   reached.
7. **The gate invented mismatches.** On a car that matched its build sheet exactly, it failed on
   `sunroof: build sheet says not present, car shows present (1.00)` and then
   `rear_badge: build sheet says none, car shows TESLA (1.00)` — on a Lincoln. An options list says
   what a car is built with, not what it may not have, so a mismatch must now name a field the sheet
   states. Separately PASS was unreachable: a clean car scored `[0.9, 0.95, 0.9, 0.9]` on its
   all-clears and went to review because 0.9 counted as uncertain, and 0.9 is the value the model
   returns most often. An all-clear at the bar now clears.

### Correction: the host is not CPU-starved

An earlier note here claimed simulated time ran *slower* than real time on a loaded server, and used
that to explain why the spacing rule's wall-clock fallback misfired. That was wrong.
`realtime_ratio()` now measures it: **10.31 simulated seconds per real second** with three cars. The
box is not short of CPU, and no instance upgrade is indicated.

The rule's bug stands, but the reason is simpler than stated: wall-clock time has no fixed
relationship to distance travelled in a simulation that runs at 10× real time, in either direction.
It is not a substitute for asking where the car is.

Still open: parallel scenario execution needs one CARLA server per slot.

**Real Gemini verified** on `id-rd-ca-hanif-ali` via Vertex AI (`gemini-2.5-flash`). Two things had
to be corrected along the way, both worth knowing:

1. `scripts/demo.py` **ignores `MARSHAL_LLM`**. It hardcodes `llm="offline"` and only uses Gemini
   with `--live`, and even then routes `visual.`, `track.` and `assist.` back to the stand-ins. An
   earlier claim that all seven flows ran on Gemini was wrong: those verdicts came from stand-ins.
2. The **fake plant cannot use real Gemini for vision at all**. `FakeGateCamera` emits
   `gs://marshal/...` URIs with no bytes (`Media.truth` is explicitly never sent to Gemini), so
   Vertex AI tries to fetch a bucket that does not exist:
   `403 ... does not have storage.objects.get access to .../buckets/marshal/objects/gate/...`.
   Flows 3 and 4 need **real CARLA frames**, which `CarlaGateCamera`/`CarlaTrackCamera` do supply
   as bytes.

Verified on real CARLA frames:

- **Flow 3** genuinely judges pixels. Against a build sheet saying `colour=black`, Gemini read the
  four real gate views and returned `observed_colour: "blue"` with a colour mismatch at confidence
  1.0, and reported no invented defects (`findings: []`).
- **Flow 4** needed three fixes before the camera half meant anything. It originally passed on
  telemetry alone while Gemini refused to confirm **any** of the four steps:
  - a single rear three-quarter view cannot see front headlights, so each step now uses the view
    that shows its lamp (`TRACK_CAMERAS`, and front/rear evidence cameras created once);
  - at noon the lamps do not read at all, so the lighting phase drops the sun
    (`--sun-altitude`, default −8);
  - reverse lamps are a few pixels at 640×360, so evidence frames are now 1280×720
    (`--evidence-width/height`).

  After those, headlights and reverse are reliably confirmed from pixels.
  `demo_carla_day2.py` records what it can judge as `CAMERA_CONFIRMABLE` / `CAMERA_AMBIGUOUS` /
  `CAMERA_NEEDS_CLIP`.

  **Both of the doubtful cases here were later settled, and neither reason above was right.** Brake
  lights are not ambiguous: with the camera attached to the car and narrowly framed, the rear bar
  reads clearly every time — what was wrong was the expectation, which asked for "left, right and
  the third (centre high) lamp" on a car that has one continuous bar. Indicators are not blocked by
  the blink phase either: setting each light state on its own and photographing the result showed
  that `RightBlinker` lights one amber segment while `LeftBlinker` renders **nothing at all**, the
  frame being identical to lights-off. One side of this asset is simply not drawn. See **Flow 4 on
  the whole R-12 routine** below.

  Narrowing the field of view to 50° was a regression **for a camera fixed to the map**: the car
  drives past it, so at some distances the frame filled with a door instead of the rear, and Gemini
  correctly reported the rear was not visible. That is a framing problem, not an argument against
  detail — and the conclusion drawn here, that detail comes from resolution rather than field of
  view, was too broad. Once the camera is **attached to the car** the distance no longer varies, and
  narrowing to 38° is what made the lamps legible enough to read: it is what showed that the brake
  expectation described a different car. Resolution alone did not do that at 90°, where a lamp was a
  handful of pixels in a wide night scene.

  CARLA's camera auto-exposure also hides the brightness
  a lamp adds (measured deltas of 0.3–0.8 on a 0–255 mean, with front `LowBeam` measuring *darker*
  than lights-off), so a brightness heuristic cannot replace the model's judgement.

- **Flow 6** generated a valid spec from plain language in one attempt with real Gemini.

Gemini also read a real CARLA frame of the corner collision correctly, returning
`{"people_present": true, "person_in_lane": true, "risk": "high"}`.
