# Marshal

Gemini makes the decisions around a car plant's autonomous driving. Finished cars already drive
themselves from the line to the yard; Marshal decides what happens at each stop (the gate check, the
lamp check, the report, the route, the parking slot, a late truck, a stuck car) and sends the car its
next mission. It never drives: it speaks one mission API to the plant's own driving system.

Built by **IONIFY** for the Google Cloud AI Builder Cup 2026.

## Branches

| Branch | What it is |
| --- | --- |
| **`main`** (this branch) | Marshal itself: the flows, Gemini, the rules, the CARLA plant and its scripts, the evaluations. Same as `rey-qais-merge`. |
| **[`faris-be`](https://github.com/Reynxzz/marshal-ai/tree/faris-be)** | **Backend**: the Marshal API (FastAPI, `src/marshal_api`) on Cloud Run, including the bridge that runs these flows with real Gemini on the browser plant's camera frames (`api/v1/sim_bridge.py`). Deploys with `.github/workflows/deploy-backend.yml`. |
| **[`frontend-demo`](https://github.com/Reynxzz/marshal-ai/tree/frontend-demo)** | **Frontend**: the supervisor dashboard and the browser plant (Three.js on CARLA's road layout), served at **[marshal.cmid.cloud](https://marshal.cmid.cloud)**. Deploys to Cloud Run with `.github/workflows/deploy-cloudrun.yml`; infrastructure in `infra/gcp` (Terraform). |

## How it works

```
plant driving system ──events──▶  Marshal  ──request──▶  Gemini on Vertex AI
   (CARLA, or the       ◀─missions──  flows + rules  ◀──answer───   3.8 Flash · 2.5 Flash
    browser plant)                     │
                                       ├─▶ Cloud Storage   the photo behind every verdict
                                       ├─▶ Firestore       live records the dashboard reads
                                       ├─▶ BigQuery        every state change, appended
                                       └─▶ a person        anything doubtful or risky
```

Every step runs the same loop: **event in → Gemini answers → rules in code check it → a person if
unsure → next mission.**

- **Decisions are one token.** Options are numbered; Gemini 2.5 Flash answers with a single token
  and its log-probabilities give every option a real probability, in about a second. "Ask a person"
  is always an option, so a doubtful case is detected, not guessed.
- **Two models, each where it measured best.** Gemini 3.8 Flash reasons and writes (the help card
  and its object boxes, the report, scenarios, the test lab). Gemini 2.5 Flash keeps the gate, the
  lamps and every decision: no Gemini 3 model returns log-probabilities.
- **Safe by construction.** Rules in code wrap every answer: never drive past a person, never send
  an uninspected car to a shipping slot, only the options the car offered.

## The steps

| # | Step | Gemini is asked | Code | Model | Measured |
| --- | --- | --- | --- | --- | --- |
| 1 | Release | (rules only) | `flows/yard.py` | — | — |
| 2 | Gate check | 4 photos + build sheet: any defect? does it match? | `flows/visual.py` | 2.5 Flash | accuracy 0.977, no false alarm (v6-gate); damage 93% found (v2-damage) |
| 3 | Lamp check | camera vs telemetry, beside a car that passed | `flows/functional.py` | 2.5 Flash | 1.0 / 0.9 (v1 / v8-track) |
| 4 | Report | write it, safety first, pick the rework bays | `flows/report.py` | 3.8 Flash | 1.0 (v3-report) |
| 5 | Route | which way, given the hazards reported? | `flows/route.py` | 2.5 Flash | 18 of 20 (`eval.routes`) |
| 6 | Park | which of up to 3 A\* paths, segment by segment | `driving/parking.py` | 2.5 Flash | 8 of 12 parked, no collision (`eval.parking`) |
| 7 | Yard re-plan | a truck is late: a new slot plan | `flows/yard.py` | 3.8 Flash | rules overrule unsafe plans |
| H | Help when stuck | 8 frames: what is there, what now? | `flows/assistance.py` | 3.8 Flash | right, box overlap 0.95 (v7-assist) |

Plus the test lab (`lab/`): an incident becomes a scenario spec, Gemini searches for variations that
break the driving, and the release gate blocks on a failing regression.

## The two plants

**CARLA 0.9.16**: we had no car plant to test in, so we built one, laid out the way plants run the
end of the line: line end, inspection gate, test track, rework bays, finishing, lane C, and the yard
with eight bays and a loading bay. `scripts/plant_xodr.py` writes the road network as OpenDRIVE (a
one-way 498 m loop with lane C across the middle); `scripts/load_plant_world.py` builds it on the
server (`MARSHAL_CARLA_MAP=MarshalPlant`); `src/marshal_ai/plant/plant_map_world.yaml` names the points Marshal knows. CARLA's own
BehaviorAgent drives; Marshal only sends missions (`driving/carla_backend.py`).

**The browser plant** (`frontend-demo`): the same roads and named points in Three.js, with a 10 Hz
kinematic-bicycle engine, a 12-channel lidar and rendered camera frames, so anyone can run a shift
without a GPU. Its frames go through the `faris-be` bridge to these same flows and the same Gemini.

## Run it

Locally, no GPU and no Google Cloud (deterministic stand-ins for Gemini):

```bash
uv venv --python 3.10 && uv pip install -e ".[dev]"
MARSHAL_LLM=offline .venv/bin/pytest
.venv/bin/python scripts/demo.py
```

With real Gemini on Vertex AI (keep the key outside the repository, e.g. `~/.config/marshal/`):

```bash
export GOOGLE_APPLICATION_CREDENTIALS=~/.config/marshal/<service-account>.json
export GOOGLE_GENAI_USE_VERTEXAI=TRUE GOOGLE_CLOUD_PROJECT=id-rd-ca-qais-jabbar GOOGLE_CLOUD_LOCATION=us-central1
.venv/bin/python scripts/demo.py --live
python -m marshal_ai.eval eval/datasets/v6-gate.json --runs 5      # score a flow against known answers
```

On a CARLA host (Ubuntu, CARLA at `localhost:2000`, `/opt/carla`; see the engineering log for host
setup):

```bash
MARSHAL_CARLA_MAP=MarshalPlant MARSHAL_PLANT_CARS=5 bash scripts/run_carla_plant.sh   # cars through the whole plant
MARSHAL_PLANT_WRONG_COLOUR=2 bash scripts/run_carla_plant.sh                          # a red car on a blue build sheet
MARSHAL_PLANT_HAZARD="finishing_corner:forklift unloading pallets" bash scripts/run_carla_plant.sh
MARSHAL_PARKING=gemini bash scripts/run_carla_plant.sh                                # Gemini parks each car
bash scripts/run_carla_day4.sh                                                        # lamp check, dark headlights
bash scripts/run_carla_day5.sh                                                        # a stuck car asks for help
bash scripts/film_hero.sh                                                             # the video's CARLA footage
```

## Settings

| Variable | Default | |
| --- | --- | --- |
| `MARSHAL_LLM` | `gemini` | `offline` for the deterministic stand-ins |
| `MARSHAL_MODEL` · `MARSHAL_DECISION_MODEL` | `gemini-3.8-flash` · `gemini-2.5-flash` | reasoning model · decision model |
| `MARSHAL_TASK_MODELS` | `visual.=gemini-2.5-flash,track.=gemini-2.5-flash` | per-task overrides |
| `MARSHAL_DRIVING` | `fake` | `carla` on a CARLA host |
| `MARSHAL_BUS` · `MARSHAL_STORE` | `memory` | `gcp`: Pub/Sub · Firestore |
| `MARSHAL_ARTIFACTS` · `MARSHAL_HISTORY` | `none` | `gcs`: Cloud Storage · `bq`: BigQuery |
| `MARSHAL_PARKING` · `MARSHAL_ROUTE` | `standin` · `reason` | `gemini` parking · when to ask Gemini for a route |

## Layout

```
src/marshal_ai/
  flows/        the steps: yard, visual (gate), functional (lamps), report, route, assistance
  driving/      mission API, rules, routines, parking planner; fake and CARLA backends
  lab/          scenario generator, adversarial search, regression suite, release gate
  llm.py        the one place Gemini is called (decisions with logprobs, JSON answers)
  plant/        plant map (named points, lanes, routes) and plant state
  events/ store/ history.py artifacts.py   Pub/Sub, Firestore, BigQuery, Cloud Storage
  eval/         scoring every flow against known answers
  dashboard.py  the minimal decision feed beside the CARLA camera
eval/datasets/  v1 … v8: gate, damage, report, scenario, search, assist, track
scripts/        CARLA host setup, plant world, run scripts, recording, replay and filming
docs/           engineering-log.md (the full build history), threejs-simulator.md
```

## More

- [marshal_flow_design.md](marshal_flow_design.md): the design.
- [docs/engineering-log.md](docs/engineering-log.md): every run, measurement and correction, as it happened.
- [docs/threejs-simulator.md](docs/threejs-simulator.md): what Marshal needs from a simulator, the brief for the browser plant.
- [TEAM_PLAN.md](TEAM_PLAN.md) · [PROGRESS.md](PROGRESS.md): who did what, and when.
