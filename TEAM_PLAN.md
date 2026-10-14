# Team plan

What is left between the system today and [marshal_flow_design.md](marshal_flow_design.md), and who
does it. Status as of 2026-10-09 is in [PROGRESS.md](PROGRESS.md).

| | Role | Owns |
| --- | --- | --- |
| **Rey** | AI engineer | Gemini's part of each flow: judgements, prompts, scenario generation, adversarial search, evaluation against the design's KPIs |
| **Qais** | Simulation engineer | CARLA: the driving system stand-in (BehaviorAgent + mission API), cameras and sensors, the plant scene and actors, stability, recording |
| **Faris** | Frontend & backend | The supervisor dashboard (`marshal-fe`) and the API behind it |

## Where we are against the design

The logic of all seven flows is built (README, "The seven flows"): rules, the yard exceptions
(late truck, closed track, full bay, line too fast), the 60 s assistance escalation, the
assistance → test lab hand-off, failed-items-only re-inspection, the 3-attempt fix loop, the rule
judge, shrinking, and the regression suite. Every flow has run once on real CARLA 0.9.16 with real
Gemini.

What is missing is mostly **the design's demo, played on real CARLA**, and the places where the
simulation cannot yet show what the design describes. The target is the design's
**five-minute demo script** (design, "Hackathon scope and demo script"):

| Demo scene | Design | Today on real CARLA | Gap |
| --- | --- | --- | --- |
| 1 Normal flow | Cars leave the line; one passes gate and track with a clean report | One car end to end on the plant world (`MARSHAL_CARLA_MAP=MarshalPlant run_carla_scene1.sh`); five cars to the yard by truck, filmed with the decision feed (`MARSHAL_PLANT_CARS=5 run_carla_plant.sh`) | The late-truck re-plan on film (fixed, not yet re-run); the gate photographs a parked car, not one rolling through at 5 km/h |
| 2 Two faults | Wrong colour flagged at the gate; dead **left brake light** (telemetry ON, camera OFF); both booked into rework | Dead **headlight** caught, repaired in the electrical bay and re-checked (`MARSHAL_DAY4_REWORK=1 run_carla_day4.sh`); wrong colour flagged at 1.00 in 3 of 3 (`MARSHAL_PLANT_WRONG_COLOUR=2 run_carla_plant.sh`) | A brake-lamp fault the asset can show |
| 3 Stuck car | Box in lane, worker nearby, "wait" recommended, case sent to the lab | Works (`run_carla_day5.sh`) | The chosen option executed by the car; the case turned into a lab scenario on CARLA |
| 4 Test lab | Plain-language request → spec → run → passes; adversarial search finds a night / pallet-stack failure, report with clip | Generation and a serial search work (`run_carla_lab.sh`) | Search over light, weather, occluder, second actor; 8 runs in parallel; failure report with its clip |
| 5 Close | Failure joins the regression suite; release gate shows "1 failing test, release blocked" | Run end to end on CARLA (`run_carla_release.sh`): blocked by the search's failure, reports in BigQuery | Release gate end to end on real runs and BigQuery history |

---

## Milestones

| | Goal | Done when |
| --- | --- | --- |
| **M1** | Dependable runs | `run_carla_all.sh` passes every flow three times in a row with no CARLA restart |
| **M2** | Demo scenes 1–3 as the design describes them | Each scene runs on real CARLA in one command, recorded and filmed |
| **M3** | Demo scenes 4–5 | The night / occluder failure found by a parallel search, its clip in the report, the release blocked |
| **M4** | Measured against the design's KPIs | Every flow's verdicts scored over many runs; numbers in a report |
| **M5** | On the dashboard | The supervisor sees and decides through `marshal-fe` (Faris, in parallel throughout) |

---

## Qais: simulation

### M1: dependable runs
- [x] **Fix the camera sensor leak.** (`qais/m1-sensor-leak`: 11 left per scene 1 run → 0) Runs leave cameras alive in the world; 28 built up in one
  session, all rendering every frame. Destroy them when each phase ends. Scene 1's gate deliberately
  skips teardown (see `capture_gate_views`), so handle that one with care.
- [x] **`scripts/run_carla_all.sh`** (`qais`): every flow in order, CARLA health-checked and restarted between
  flows, each recording filmed at the end. One command to verify any change.
- [x] **Find what crashes the renderer.** (`qais`: `-quality-level=Low` itself; the server now runs at Epic) A camera west of the yard lane looking west
  (`(221, -27, 7)`, yaw 163°) segfaults the server even with no replay. Grid-probe to the asset or
  area, then try `-quality-level` (now `Low`). May also be what crashed scene 1's gate.
- [x] **Crash-on-exit of the CARLA client** (`qais`: demos exit via `exit_without_teardown`) (`terminate called without an active exception` after a
  phase finished). Scene 1 tolerates it; fix the cause so other flows don't need the workaround.

### M2: the plant behaves as the design describes
- [x] **Several cars at once** (scene 1). (`qais`: `run_carla_plant.sh`, 3 cars to 2 trucks, all shipped) Cars leave the line on the design's rules: fewer than 2 in
  the gate queue, 25 m apart, slots by truck nearest the loading bay (design, flow 1).
- [ ] **Missions that move cars.** Yard re-slotting after a late truck changes only the records
  today; the cars should drive to their new slots (design, flow 1 "Send missions").
  *Rey, for review:* an approved re-plan sends a real mission per car, a car whose new slot is still
  taken waits for it to empty, and a plan that would park an uninspected car is rejected (02391ae).
  Not yet shown on CARLA: the filmed run's plan named a stale car and was rejected (fixed, 03dbc4a).
- [ ] **Drive-through gate.** The design photographs the car rolling through at 5 km/h with four
  cameras (flow 3). Today the car stops, because a capture and a running driving loop in one process
  deadlock (comment in `run_carla_scene1.sh`). Find a way: a capture process separate from the
  driving process, or sensors that stream into a buffer.
- [x] **A wrong-colour car** at the gate (scene 2) (`qais`: `MARSHAL_PLANT_WRONG_COLOUR=2 run_carla_plant.sh`; flagged at 1.00 in 3 of 3 runs, booked to production control): spawn a car whose colour or model differs from its
  build sheet. The variant check is the stand-in the design names for CARLA (flow 3, "What is real").
- [ ] **A brake-lamp fault the camera can see** (scene 2 wants a dead left brake light). This asset
  draws one continuous brake bar and no left blinker (README, "Flow 4 on the whole R-12 routine").
  Find a blueprint with separate lamps, or document why the headlight stays the demo fault.
- [ ] **Track steps that actually happen.** The car is held still during the routine, so reverse
  does not move it and the brake test (step 5) and full-lock turn (step 6) are telemetry-only. The
  design puts the brake test in CARLA scope (flow 4, "What is real"): drive it from 20 km/h and
  measure the stopping distance; roll the reverse 3 m.
- [ ] **Rework bays on the map.** Cars driven to the bay the router booked, then back to the gate for
  re-inspection of the failed items (design, flow 5).
  *Rey, for review:* runs on the plant world for the lamp fault (electrical bay, back round to the
  gate, step 1 re-run, PASS; film pw-day4). Cars now square up on a slot or bay once they reach it.
- [ ] **The chosen assistance option executed.** After `assist.choose`, the car does what was chosen
  (wait, pass in the left lane at 3 km/h, reroute), driven by BehaviorAgent (design, flow 2).
  Today the `AssistChoose` branch in `driving/carla_backend.py` only clears the case; the
  translation of each option is still a placeholder ("day 3: translate the chosen option here").

### M3: the test lab at the design's scale
- [ ] **The full search space** (design, flow 7 table): light noon/dusk/night, weather
  clear/rain/fog, occluders none/forklift/parked car/pallet stack, a second actor (car ahead
  braking), worker speed and trigger distance, ego speed, all compiled and runnable.
- [ ] **8 runs in parallel** for the 60-run budget. Several CARLA servers on the one T4, or one per
  run on more GPUs; measure speed and stability. `run_batch` already runs batches in parallel.
- [ ] **A clip per test run.** The design keeps a clip with every test run and every failure report
  (records table, "Test run"). Record each variant and film the failing one, reusing
  `run_carla_replay.sh`.

---

## Rey: AI

### M2: the judgements in demo scenes 1–3
- [x] **Wrong colour flagged** at the gate on Qais's car, with the finding written as the design's
  structured output (zone, type, confidence, image) and the verdict from the 0.6 / 0.9 thresholds.
  *Done:* flagged at 1.00 in 3 of 3 plant runs (Qais). Each mismatch is now also a structured finding,
  `InspectionResult.mismatch_findings` (zone `whole_car`, type `wrong_colour`, confidence, the side
  photo, the text as observation); the text list stays for the code that reads it (3bdf1ec).
- [x] **Lamp check in daylight.** Healthy cars failed the headlight check on the plant world ("off,
  just as in the reference"). The reference and the car under test are cropped to the lamps and sent
  as one high-resolution image, and the check looks only at the lamps the step names. Dataset
  v8-track (healthy and dark lamps, sun 15 / 45 / dusk): 0.44 -> 0.90, every dark lamp caught.
- [x] **Gemini as a decision model.** Judgements that are a choice answer one option number, the
  probability from its log-probability (`llm.Decision`, `MARSHAL_DECISIONS`). Default for the lamp
  check (0.99 s against 3.2 s, no wrong verdict) and the gate's screens (same accuracy as full
  answers). Gemini now runs in asia-southeast1, nearest the CARLA host: a call 0.9 s against 2.4 s.
- [x] **Scratches and dents** from a public car-damage image set (the design's stated stand-in,
  "What is real"): findings per zone for the eight body zones, against labelled ground truth.
  *Done:* dataset v2-damage (Humans in the Loop, CC0): 30 damaged photos, 16 clean crops.
  gemini-2.5-flash finds 97% of damage, right kind 95%, right zone 100%, 13.7 s a photo;
  flash-lite (thinking 1024) 89%, 4.9 s. False alarms need truly clean panels (see README).
- [x] **Gate under 5 s** (design, "A car's journey"). Its five Gemini calls run one at a time
  because `google-genai` misbehaved from five threads; retry with a current SDK or the async client.
  *Progress:* calls run at once again (lock removed, schema sent as JSON): 33 s → 12 s on
  gemini-2.5-flash, same answers. Then thinking capped at 512 tokens for the gate's tasks and a
  stalled call re-sent after 10 s: v6-gate median 5.6 → 5.3 s, 90% within 7.3 s (was 8.6), same
  answers, damage recall unchanged. *Accepted at 5.3 s median* (Rey, 2026-10-10): a single flash
  call on 1280x720 frames takes 4-6 s, and lower resolution was faster but missed scratches.
- [x] **No false alarms on clean cars at the gate** (found in Qais's multi-car runs, about one in
  three). *Done:* dataset v6-gate (`scripts/capture_gate_set.py`: five colours x six suns, plus
  Qais's plant cars). Each finding is checked against the same camera's photo of the latest car that
  passed: false findings on clean cars 6.7% -> 0%, first-pass yield 0.93 -> 0.97, wrong colour still
  10/10, real damage still 93% on v2-damage. The first car of a run has no reference; once a
  supervisor clears it, it becomes one.
- [ ] **Brake-lamp fault judged** on Qais's asset, with the 3 s clip per step, so scene 2 can use the
  design's dead left brake light.
- [x] **Rework loop on real runs**: the summary orders safety items first, picks the bay from the
  design's table, and re-inspection after repair turns the report to PASS.
  *Done:* `MARSHAL_DAY4_REWORK=1 run_carla_day4.sh` closes the loop on CARLA (dark lamp → electrical
  bay → repair relights it → step 1 only re-run → PASS, twice). Bays now come from the defect's
  words (`bay_for`): 13% of real findings used to miss the table. Dataset v3-report: right bays,
  safety first, summary leads with safety, all 1.0 on gemini-2.5-flash. For Qais: `repair()` in
  `carla_backend.py` and the `--rework` flag in `demo_carla_day4.py` are new.
- [x] **Assistance card content** (design, flow 2): the one-line situation, the object located in the
  clip (for the dashboard to box), ranked options with reasons, confidence, and never recommending a
  high-risk option.
  *Done:* `AssistanceCase.objects` holds each box (label, kind, frame, `image` URI, `box_2d`
  [ymin, xmin, ymax, xmax] 0-1000, distance), from a separate `assist.locate` call; the boxed frame is
  uploaded. Scene 3: right recommendation 5/5, worker boxed well (IoU 0.78), box loosely (0.25).
  Card time: 13 s -> 4.3 s median (19 of 22 under 5 s) on dataset v7-assist (11 CARLA stops, 8 with
  a person in view) with the assessment's thinking off, same answers; the situation line now names
  every person in view (16 of 16).
- [x] **Persist proposals.** Supervisor proposals (e.g. the late-truck re-plan) live only in the
  running app; there is no Firestore collection for them. Add one beside `COLLECTIONS` in
  `src/marshal_ai/store/__init__.py`; Faris's `/proposals` page reads it.

### M3: the test lab story
- [x] **Incident → test on CARLA.** Scene 3's assistance case becomes a scenario spec, validated,
  compiled and run; the design's ops-to-lab loop ("The loop between operations and the test lab").
  *Done:* `scripts/run_incident_to_test.sh`. Scene 3's case → SC-0142 first try → run on CARLA →
  in the library, tagged from-incident. It fails `no_contact`: the car drives into the box (see
  Qais hand-off). Generation went from 0/3 to 12/12 on dataset v4-scenario, 100% faithful.
- [x] **Search that reads results.** Gemini proposes each batch from the previous batch's scores and
  clips and says why ("gap shrinks at night; try tall occluders next"), within the 60-run budget.
  *Done for scores:* measured against random search on dataset v5-search: first failure in 11 runs
  vs 19, 2.9 vs 2.0 failures per 100 runs, faster on every scenario with a failure. Each run keeps
  its reason (`TestRun.reason`). Clips wait on Qais's clip per test run.
- [x] **The failure report** in the design's form (flow 7 example): conditions, measured gap vs
  limit, likely cause, suggested fix, with the clip.
  *Done:* conditions, measured vs limit and "it passes with ..." come from the runs, not the model;
  Gemini gives the cause and a concrete fix (zone, condition, km/h), which is tried in the simulator
  on the failing and the passing case. Dataset v5-search: fix proposed 1.0, fix works 1.0. Stored as
  `Scenario.failure_report`. On CARLA the fix is reported "not tried" (the runner cannot change the
  driving system); the clip waits on Qais's clip per test run.
- [x] **Release gate end to end** (scene 5): the failure enters the regression suite, BigQuery
  history is read, and the gate reports "1 failing test, release blocked".
  *Done:* `run_carla_release.sh` on CARLA with Gemini: the search failed SC-0142-v1 in 2 runs, it
  joined the suite, and the gate reported "2 tests, 1 passed, 1 failed. 1 failing test, release
  blocked." Release reports are stored (`releases`) and written to BigQuery; the previous release is
  read back from there. Release 1.1 (the fix) runs only on the quick simulator.
- [x] **A retrieval library for generation**: validated specs as examples for new requests, as in
  ChatScene (design, flow 6 "The method Marshal keeps").
  *Done:* `lab/library.py`: five checked seeds plus every reviewed library scenario; a request is
  shown the two most like it. Dataset v4-scenario (8 requests, both ways): first try 18/24 vs 15/24
  with the fixed examples, mean attempts 1.33 vs 1.58, all generated and faithful. Small gain; the
  previous run tied. The fix loop now sees all earlier errors.

### M4: measured against the design
- [ ] **Evaluation harness.** Run each flow N times and compute the design's KPIs:
  flow 1 cars per hour, line-to-yard time, cars waiting; flow 2 time to resolution; flow 3
  first-pass yield, review share, confirmed defects vs false alarms; flow 4 disagreements between
  telemetry and camera, failures per item; flow 5 rework rate, cars shipped with an open finding
  (target 0); flow 7 failures per 100 runs, time to first failure. It runs mostly on saved images
  and the offline runner, so it needs little CARLA time.
  *Started:* `python -m marshal_ai.eval` covers flows 2, 3 and 4 on dataset v1 (7 cases from
  the 2026-10-09 runs); v2-v5 add damage, reports, generation and search.
  *Operational KPIs done:* `python -m marshal_ai.shift` runs a 40-car shift on the simulated plant
  and reports flows 1, 2, 3, 4 and 5 against each car's real faults. 10 shifts: no faulty car
  ships with a perfect gate; with Gemini's measured damage recall 16 of 68 damaged cars ship while
  "shipped with an open finding" reads 0. It found and fixed two bugs: cars that found the yard
  full were never shipped, and the simulated plant missed every dead lamp after the first car.
  *Still to come:* a larger image dataset (needs CARLA frames from Qais's cars and faults).
  *Since:* v6-gate (gate, 43 cases), v7-assist (assistance, 11 stops), v8-track (daylight lamps);
  every eval runs with decisions on or off and in either region.

---

### Plant world and films (Rey, 2026-10-10 to 10-14)
- [x] **The plant's own world** (`MARSHAL_CARLA_MAP=MarshalPlant`): a 498 m OpenDRIVE loop with the
  gate, track, five bays, eight slots and the loading bay; CARLA's factory halls, concrete slabs,
  bollards, trucks and workers around it. The replay places the halls and slabs itself (the replayer
  strands static meshes).
- [x] **Films that explain themselves**: the demos note each decision on the simulator's clock and
  the replay draws a feed of them (YARD RULES / GEMINI / SUPERVISOR); `--look cinematic` for a low
  camera, late sun, wet concrete and 1080p.
- [x] **Five cars to the yard on the plant world.** For Qais to review, in `driving/`: parked cars
  are not traffic for BehaviorAgent or the obstacle check; yard slots moved past the bend; a car
  squares up on its slot or bay. Without these, the third car stopped behind the second at the yard.
- [ ] **The late-truck re-plan on film**, and a clip per step of every flow (backlog). Need the VM.

## Faris: dashboard

The design's supervisor dashboard is where people use the **human authority** table: they see
routine releases, approve re-slots, choose assistance options, confirm rework, review scenarios and
approve releases.

### M5, in parallel from now
- [ ] **Read-only API** (`/api/v1`) serving the GET endpoints `marshal-fe` already calls
  (`src/lib/api.ts`) from Firestore (`cars`, `assistance_cases`, `inspection_results`, `car_reports`,
  `scenarios`, `test_runs`), BigQuery (KPIs, history) and Cloud Storage (photos and clips as signed
  URLs). The contract is the frontend's Zod schemas (`src/lib/schemas.ts`), checked by contract tests
  using the mock fixtures. `/events` reuses the feed in `marshal_ai.dashboard`.
- [ ] **The design's screens, on real data**: the assistance card (one-line situation, clip with the
  object boxed, ranked options); the per-car report (each finding with its photo or clip, the
  rework, the re-inspection); the test lab (variant grid by light and trigger distance, the failure
  report with clip); the release gate.
- [ ] **Decisions** as POST endpoints that publish commands to Pub/Sub. The API never drives anything
  directly, matching the design's rule that agents (and the dashboard) only send missions.
- [ ] **Video**: replay clips per car and per test run, and the live camera when Qais has it.

---

## Working in parallel

Rey and Qais can work at the same time. The limit is **one CARLA server**, which runs one scenario
at a time, so the code work is parallel and the server time is shared.

- **Qais has CARLA priority**: nearly all of his work needs it. Rey takes short slots to confirm on
  the simulator. Announce "on CARLA" in the team chat; a lock file the run scripts check would
  enforce it later.
- **Rey works mostly off the server**: the offline fake backend and runner, saved gate and track
  photos from Cloud Storage, and the public damage image set.
- **Split the code by area**:
  - Qais: `driving/`, `carla_*`, `cameras.py`, `plant/` (incl. `plant_map.yaml`), `lab/compiler.py`,
    `scripts/run_carla_*.sh`, the recorder and replay
  - Rey: `flows/`, `llm.py`, `lab/generator.py`, `lab/adversarial.py`, `lab/regression.py`, `store/`,
    a new `eval/`
  - shared, agreed before changing: `scripts/demo_carla_*.py`
- Short branches off `feat/carla-gemini`; Rey and Qais review each other's; offline tests green
  before merging.

### Hand-offs

| From → to | What | Unblocks |
| --- | --- | --- |
| Qais → Rey | Wrong-colour car, brake-lamp asset | Scene 2 judgements |
| Qais → Rey | Assistance options executed in CARLA | Scene 3 end to end |
| Qais → Rey | Full search space compiled; parallel servers; a clip per run | Search that reads results, failure report with clip |
| Rey → Faris | Record shapes, the `proposals` collection, the assistance card fields (`AssistanceCase.objects`: draw `box_2d` on `image`, scaled from 0-1000), `TestRun.reason` (why the search tried each variant), `Scenario.failure_report` (conditions, measured, passes_when, likely_cause, suggested_fix, speed_fix, fix_check, clip_uri), the `releases` collection (`ReleaseReport`: passed, failed, newly failing/passing since `previous_release`, approved_by) | Dashboard screens |
| Qais → Faris | Replay clips per run, a live camera | Video on the dashboard |
| Rey → Qais | BehaviorAgent drives into static props: SC-0142 (scene 3's box) fails `no_contact` on CARLA. The stand-in needs to brake for objects in the lane, as the plant's driving system would | Scene 3 and its test passing |
| Rey → Qais | The VM's `.env` (`~/marshal-app`) points `GOOGLE_APPLICATION_CREDENTIALS` at a laptop path; scripts that do not export it themselves (`run_carla_lab.sh`) cannot reach Gemini | Lab runs with Gemini on the VM |
| Rey → Qais | Small changes in your files: `repair()` in `carla_backend.py`, `--rework` in `demo_carla_day4.py`, `MARSHAL_LAB_SOURCE` in `run_carla_lab.sh`, the contact event in `lab/compiler.py` | Review |
| Rey → Qais | Every run now dresses Town05 as a plant before recording (`dress_plant.py`, called from `carla_recording.sh`; `MARSHAL_DRESS=0` skips it; tagged `marshal-dressing`, none on a plant lane). Films are labelled (`film_labels.py`) and need Pillow: `install_carla_mission_runtime.sh` now installs it - please re-run it on the host | Plant scenes and filmed runs |

## Suggested first week

1. **Qais**: sensor leak and `run_carla_all.sh` (M1); then several cars at once and the
   wrong-colour car.
2. **Rey**: persist proposals; the damage image set and the gate's zone findings; the evaluation
   harness on saved gate and track photos.
3. **Faris**: the read-only API for `/overview`, `/cars`, `/cars/{vin}`, `/cars/{vin}/report`,
   `/inspection/results`, `/lab/scenarios`, with contract tests, then mock mode off in a preview.
