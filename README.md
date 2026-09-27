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
For now the manifest deterministically verifies JPEG presence, telemetry and CARLA light state;
real image judgement is deferred to Gemini. Next implement physical reverse/brake/full-lock
maneuvers, then enable Gemini, Pub/Sub and Firestore independently.

Do not send a CARLA mission to any named point whose `carla` transform is still null.

## Status

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
ticks old, so it cannot distinguish a lamp that has just changed from one that has not. A step
needing an exact moment needs a clip, which is still open.

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
| 6 generate | `run_carla_lab.sh` | `SC-0142` generated in 1 attempt, dry run clean, judged `passed: true`, moved to `in_library` |
| 7 adversarial | `run_carla_lab.sh` | search shrinks the failure to one parameter (`trigger_distance_m` 9 → 7) |

Flow 2 was broken by the camera work and is now fixed properly: the assistance frame comes from a
camera **on the car**, created with it and streaming for as long as it exists. It used to be taken by
spawning a camera behind the car at the moment it stopped, which cannot work — the stall is detected
inside the driving loop, so the loop was blocked waiting for a frame only it could produce. That
failure was swallowed, the request went out carrying a `gs://` name nothing had been uploaded to, and
it surfaced as a **403 from Vertex AI** trying to read it. Media with no bytes is no longer presented
as a clip.

## Flow 4 on the whole R-12 routine, and what a still cannot show

All six steps were run against real CARLA for the first time. Three failed on a healthy car:

| Step | Camera | Why |
| --- | --- | --- |
| 1 headlights | confirmed | a lit lamp is a state a frame holds |
| 2 indicators | **cannot be judged** | a blinker is lit for only part of its cycle |
| 3 brake_lights | confirmed | all three lamps read on the rear of the `mkz_2020` |
| 4 reverse | confirmed | the reverse lamp is a state |
| 5 brake_test | **cannot be judged** | a stopping distance is a path, not a state |
| 6 full_lock | **cannot be judged** | so is a turning circle |

Gemini answered "not seen" at confidence 1.0 for those three, which is a fair reading of the frame
and the wrong verdict for the car. The design already calls for a 3 s clip per step for exactly this
reason; until clips exist, a step whose evidence cannot settle the question is recorded as
**unconfirmed** rather than failed, and each step says whether one frame could show its result.

The verdict now has three outcomes, because there are three cases: a camera that **contradicts**
telemetry is a FAIL, evidence that could not settle it is a REVIEW, and only a car where both
sources agree on every step PASSes unattended. The full routine now returns REVIEW with steps 1, 3
and 4 confirmed; scene 1 runs step 1 and still passes unattended.

Note that brake lights do read on this vehicle, correcting an earlier note here that called them
ambiguous.

## Still open against the design

| Design says | Status |
| --- | --- |
| 3 s clip per track step (flow 4) | **Not built.** Steps 2, 5 and 6 cannot be confirmed without it and are marked unconfirmed. |
| 10 s clip with the assistance request (flow 2) | **Stand-in.** One still forward view from the car's own camera; the desk's decision was correct from it. |
| 60 runs per scenario, 8 in parallel (flow 7) | **Serial only.** Parallel runs need one CARLA server per slot; the search works but takes the full budget serially. |
| Release gate blocks a release on a failing test | Implemented in `lab/regression.py`; not yet exercised end to end against BigQuery-backed history. |
| Demo scene 2 uses a dead brake light | Built with a dead **headlight**: it is the fault the camera reads most clearly, and the disagreement is the point. |
| Scratches and dents from a car-damage image set | Not wired in; the gate currently finds real colour and variant mismatches only. |
| Gate decides in under 5 s (flow 3) | Its five model calls now run one at a time, so the gate takes about their sum. `google-genai` proved unsafe to drive from five threads: runs failed intermittently with pydantic rejecting a JSON schema as a model, a schema from one call being handled as another's. The phased demo can afford it because the car is stopped; overlapping them again needs an SDK that supports it. |

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
same slot. Getting there uncovered six faults, none of them cosmetic:

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
6. **The gate invented mismatches.** On a car that matched its build sheet exactly, it failed on
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

  After those, headlights and reverse are reliably confirmed from pixels, brake lights are
  ambiguous (the `mkz_2020` rear light bar reads as a running light in some runs) and indicators
  cannot be confirmed from a still at all, because a blinker is only lit for part of its cycle.
  `demo_carla_day2.py` records this as `CAMERA_CONFIRMABLE` / `CAMERA_AMBIGUOUS` /
  `CAMERA_NEEDS_CLIP`.

  Narrowing the field of view to 50° to gain detail was a regression: it filled the frame with the
  car's door instead of its rear, and Gemini correctly reported the rear was not visible. Detail
  comes from resolution, not field of view. CARLA's camera auto-exposure also hides the brightness
  a lamp adds (measured deltas of 0.3–0.8 on a 0–255 mean, with front `LowBeam` measuring *darker*
  than lights-off), so a brightness heuristic cannot replace the model's judgement.

- **Flow 6** generated a valid spec from plain language in one attempt with real Gemini.

Gemini also read a real CARLA frame of the corner collision correctly, returning
`{"people_present": true, "person_in_lane": true, "risk": "high"}`.
