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

Not yet verified: flows 1 and 2 against real CARLA. `carla_backend` still notes that raising
`assist.request` when BehaviorAgent blocks on an obstacle is unwired, so flow 2 has no real trigger.
Parallel scenario execution needs one CARLA server per slot. Six plant points remain unpinned
(`loading_bay`, `yard.Y4`–`Y8`).

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
