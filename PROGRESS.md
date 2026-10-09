# Progress

## 2026-10-09 — CARLA + Gemini on one branch, every flow re-run on AWS, runs filmed

Branch `feat/carla-gemini`: the real-CARLA lab branch (`feat/carla-real-server-lab`) merged with
the Gemini/Vertex work, MetaDrive removed. 213 tests pass offline
(`MARSHAL_LLM=offline python -m pytest -q`).

### Where it ran

| | |
| --- | --- |
| Host | AWS `ap-southeast-1`, g4dn.xlarge (Tesla T4), instance `carla-simulator` from the prepared AMI `ami-05efefb6b51775a42` |
| Simulator | CARLA 0.9.16, Town05, headless under systemd (`carla`, `carla-viewer`) |
| Model | Gemini 2.5 Flash via Vertex AI |
| Records | Firestore (live), Cloud Storage (evidence), BigQuery (history) |
| Infra | `marshal-tf` (shared Terraform state in S3); SSH allowed from an allowlist in `terraform.tfvars` |

Host setup beyond OPERATOR.md: the merged branch needs `python-dotenv` (imported by
`marshal_ai.config`) and, for filming, `imageio-ffmpeg`; both go in `/opt/marshal-runtime/vendor`:

```bash
sudo pip3 install --target /opt/marshal-runtime/vendor --upgrade python-dotenv truststore imageio-ffmpeg
```

### Every flow, re-run on real CARLA and real Gemini

| Flow | Script | Result |
| --- | --- | --- |
| 1 yard | `run_carla_yard.sh` | `success`: VIN-Y1 and VIN-Y2 released under the spacing rule, VIN-Y3 held; truck delay → Gemini re-plan → approved |
| 2 assistance | `run_carla_day5.sh` | `success`: worker near the stuck car, recommends `wait_for_removal` |
| 3 visual | `run_carla_day2.sh` | `success`: four gate views, lighting steps 1-4 |
| 4 functional | `run_carla_day4.sh` | VIN-OK `PASS`; VIN-DARK `FAIL` on the telemetry/camera disagreement → `rework.electrical` |
| 3 + 5 journey | `run_carla_scene1.sh` | gate `PASS`, headlights `PASS`, report `PASS`, shipped on T-1 |
| 6 + 7 lab | `MARSHAL_LAB_ADVERSARIAL=10 run_carla_lab.sh` | SC-0143 generated in one attempt, judged `passed`, `in_library`; the search fails it at `ego_speed_kmh` 10 |

No CARLA restarts during any of these runs.

### New: recording and replay

Runs could not be watched: every run script stops the viewer (its camera and a synchronous run do
not mix), and the viewer is a fixed camera over one road anyway. Runs are now recorded on the
server and filmed afterwards. See **Recording and replaying a run** in the README for usage.

- `scripts/carla_recording.sh` — sourced by all six `run_carla_*.sh`; starts CARLA's recorder after
  the map loads, stops it in the EXIT trap. Files go to `/var/lib/carla/recordings/` (the only place
  the sandboxed `carla` service may write). `MARSHAL_RECORD=0` turns it off.
- `scripts/carla_recorder.py` — start / stop / info for the server-side recorder.
- `scripts/replay_carla.py` + `scripts/run_carla_replay.sh` — film a recording into an MP4.
  Two passes: a scout pass with no camera notes which car drives when and where; the filming pass
  opens on a wide still of all the cars, then gives each drive a still camera beside the road,
  framed to fit it. Still stretches are cut (`--keep-idle` keeps them), playback is 4×.

The design is shaped by what failed along the way:

| Tried | Why it did not work |
| --- | --- |
| Chase camera behind the car | The cars drive at ~5 km/h; the car sat frozen mid-frame (≈1 % change between frames). |
| Camera that pans to follow the car | Segfaulted the server on every replay. |
| First camera 200 m over the map origin | Segfaulted the server: it makes CARLA stream most of Town05 in at once. |
| Film every second of the recording | A 2-minute yard run has ~20 s of driving; the rest is Gemini and the records. |

### Bugs found and fixed

- **Gate photos taken at night.** The track phases drop the sun to -8° for the headlight check and
  nothing raised it again, so a gate run after any track run sent four night-time photos and Gemini
  answered `REVIEW` ("too dark to identify defects"). The scene 1 gate phase now sets daylight
  first (`--gate-sun-altitude`, default 45°).
- **Day 4 reported stale verdicts.** It waits for its result and report to appear in Firestore and
  never cleared old ones, so a previous run's records ended the waits at once: verdicts in 7 s, no
  frame taken. It now drops them first, as scene 1 already did; the re-run took 56 s and produced
  fresh evidence frames.
- **Scene 1 failed after a finished phase.** The CARLA client can abort while the interpreter shuts
  down (`terminate called without an active exception`) after the phase has stored its result. The
  runner now carries on when the phase reported done, and fails only when it did not.

### M1 check on branch `qais`

`MARSHAL_ALL_RUNS=3 bash scripts/run_carla_all.sh`: **18 of 18 flow runs passed, 0 CARLA crashes,
0 exit-time aborts, 33 min**, with every scene 1 run shipping its car. That is TEAM_PLAN's M1 "done
when" (every flow three times in a row with no CARLA restart). The same check before the exit fix
had 17 of 18: one day 4 run aborted on exit (134) after reporting success.

### Known issues

- ~~**A camera view that crashes the server.**~~ Found on `qais`: it is `-quality-level=Low`. At Low,
  any camera looking towards the far west of Town05 segfaults the server, from anywhere along the
  plant and at any time of day; looking down at the road, or cropping out the horizon, avoids it. At
  Epic every crashing pose renders and all six flows pass and film, including scene 1, whose replay
  had failed at Low. `scripts/set_carla_quality.sh` switches the service (a systemd drop-in);
  `prepare_carla_host.sh` and the Terraform bootstrap now use Epic. Cost: day 2 and scene 1 take
  ~10-50 % longer.
- ~~**Leaked camera sensors.**~~ Fixed on `qais/m1-sensor-leak`: one scene 1 run used to leave 11
  cameras in the world; two runs now leave 0. Marshal's sensors are tagged `marshal-sensor`, a car's
  three cameras are destroyed with it, and each backend clears tagged leftovers when it connects.
- **Day 4 replays in daylight.** The recording does not restore the weather, and the replay sets
  daylight, so the lamps do not show; the evidence frames in Cloud Storage
  (`track/<VIN>/step1_headlights.jpg`) are what show them.
- **Python 3.10 on the host** draws end-of-support warnings from `google-api-core`; harmless for now.
- **Flow 7 is serial**, as before: one CARLA server runs one scenario at a time.

### Next

- Live view in the dashboard: a camera owned by the process that ticks the world, streamed as
  MJPEG into the dashboard's camera pane (recording + replay stays for shareable videos).
- Upload replays to Cloud Storage (`replays/`) so they outlive the instance and the dashboard can
  link them.
- Fix the sensor leak; find what in the west-facing view crashes the renderer.
