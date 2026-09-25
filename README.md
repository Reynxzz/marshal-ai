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

## GPU VM (CARLA + GCP)

```
uv pip install -e ".[gcp,carla]"
export PYTHONPATH=$CARLA_ROOT/PythonAPI/carla:$PYTHONPATH
python scripts/pick_carla_points.py --map Town05     # fill `carla:` in plant_map.yaml
python scripts/create_pubsub_topics.py --project $GOOGLE_CLOUD_PROJECT
MARSHAL_DRIVING=carla MARSHAL_BUS=gcp MARSHAL_STORE=gcp adk web apps
```

## Status

Verified locally (tests and `scripts/demo.py`): all seven flows on the fake plant with offline
stand-ins, including the full rework loop, bay-full handling, assistance escalation, the fix loop,
adversarial search with shrinking, and the release gate.

Not yet verified: anything against real Gemini, the CARLA backend, CARLA cameras and runner, and the
Pub/Sub and Firestore backends. Map points still need CARLA transforms. The compiled scenario
script uses proxy blueprints for forklifts and pallet stacks, and only walkers move on trigger.
