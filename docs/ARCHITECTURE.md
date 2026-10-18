# Architecture

## Shape

```text
Browser (one tab)
├─ src/sim/      deterministic engine + flows (pure TypeScript)
│   engine/vehicle  fixed-step 0.1s, kinematic motion over the world map, seeded RNG
│   flows/          flow 1-5: yard, assistance, visual, functional, report
│   lab/            flow 6-7: scenario generator, adversarial tester
│   batch/          manifest + labels, orchestrator (state machine), counts, feedback, report
├─ src/render/   three.js scene + offscreen capture (gate 4 views, track per step) -> JPEG
├─ src/ui/       dashboard components (React)
└─ src/stores/   zustand batch store; drives the engine loop via requestAnimationFrame

Server (TanStack Start server functions)
├─ src/ai/            ADK agents + aiTask() server function -> Gemini
│                     (offline only when VITE_AI_OFFLINE=true; otherwise errors surface)
└─ src/lib/serverFns/ Marshal server functions -> Gemini (fallback to deterministic offline)
```

## Marshal contract and server functions

- `src/lib/marshal/types.ts` is the TEAM_PLAN contract between the Marshal orchestrator and the
  driving simulator (`SimCommand`, `SimEvent`, `DrivingSystem`) plus the ports the flows need
  (`GateCapturePort`, `EvidencePort`, `ParkingPort`) and the server-function result types. Every
  result carries `degraded: boolean`.
- `points.ts` holds the named map points (CARLA frame, yaw in degrees), `schemas.ts` the zod
  input schemas, `eventBus.ts` a dependency-free typed emitter (`sim:*`, `marshal:command`,
  `ui:note`) and `parking/astar.ts` a pure hybrid A* planner that returns up to 3 candidate
  control sequences into a slot.
- `src/lib/serverFns/{gate,lamp,assist,parking,route,yard}.ts` are `createServerFn` RPCs:
  `evaluateGatePhotos`, `evaluateLampEvidence`, `analyzeAssistClip`, `planParkingStep`,
  `chooseRouteHazard`, `replanYardSlots`. `gemini.ts` is the only Gemini/env access; it is
  imported lazily inside the handlers. `logic.ts` holds the pure offline answers and the safety
  rules every Gemini answer is re-checked against (offered options only, colour mismatch always
  fails, parking paths always come from A*, yard moves must be legal).
- The browser calls them only through `createMarshalAi()` (`client.ts`), which falls back to the
  offline answer when an RPC throws.

## Marshal orchestrator and flows

- `src/lib/marshal/orchestrator.ts` (`createOrchestrator`) subscribes to a `DrivingSystem`,
  mirrors its events (`sim:*`) and every command (`marshal:command`) on the bus, keeps the
  dashboard state (`state.ts`: cars, decisions, assist cases, 8x10 yard, KPIs) and runs the
  flows in `flows/`: gate (M3.4), lamp + rework recheck (M5.6), route at `report_point`
  (M9.1), parking loop (M7), assist cases with "Pause when needed" (M6/M8.8) and yard re-plan
  validation and dispatch (M9.3). `hazards.ts` stages the Scene A forklift hazard (M9.4).
- It reaches Gemini only through the `MarshalAi` interface. Yard row A columns 1-8 map to the
  sim points `yard.Y1`..`yard.Y8`.
- `testing/scriptedSystem.ts` is a deterministic, timer-free `DrivingSystem` (plus gate,
  evidence and parking ports and offline truth hints) used by tests and the demo until the
  three.js engine lands; `testing/offlineAi.ts` is a `MarshalAi` backed by the offline logic.
  Its frames are placeholders, so the dashboard store keeps the gate, lamp and assist image
  checks offline for it (`placeholderFrames`); route, parking and yard still go to Gemini.
- Offline answers fail closed: without truth hints the gate fails with `not_verified_offline`
  and the lamp step reads `UNVERIFIED`, and the car is held in the rework bay for a person
  instead of passing. Cars held with nothing left to automate (full yard, parking abandoned)
  count as finished so the shift completes; pre-parked cars are not counted as produced.
- Slot assignment prefers row-A slots with both neighbours free (the slots sit nose to tail),
  and yard re-plans treat slots a car is driving to as reserved.

## Data flow

1. Operator picks 1–6 cars → `planBatch()` picks cases from the 20-case library (Gemini
   `batch.plan` when a key is set, validated in code; seeded sample otherwise) → `BatchRunner`
   builds a deterministic `Manifest` (`VIN-B0x-n`, build sheet, colour, truck, expected label +
   case id; at most one incident).
2. The runner steps a `SimEngine` at a fixed 0.1 s and drives each car through the flows:
   line end → inspection gate → test track → report → yard → shipped, with flows 1–5 active.
3. Whenever judgement is needed, the flow asks the injected `AiTaskFn` (client wrapper). Media
   frames come from the injected `CapturePort`. The engine never imports three.js or React.
4. Events become `BatchEvent`s (`flow`, `source`, `title`, `detail`, `level`, `payload`).
5. If the batch had an incident, flows 6–7 run after all cars finish; otherwise their counts are
   `not_triggered`.
6. `buildReport()` assembles KPI counts (computed in TypeScript), label outcomes, the Gemini
   `BatchFeedback` (7 per-flow entries) and the event log. The dashboard stops updating once the
   status is terminal.

## Batch status contract

```text
queued → running → done
                 ↘ failed
                 ↘ cancelled

progress = finished_cars / requested_cars * 100
finished = car shipped, parked in yard, or done reworking
```

## Important design decisions

- **Client-side simulation.** Vision capture needs no headless browser, the viewer runs at 60 fps
  from the same state, and replay only needs the seed. Consequence: closing the tab stops the
  batch (accepted for the demo). The dashboard route is client-rendered (`ssr: false`)
  because the SSR dev-hydration path in this TanStack Start/Vite combination does not attach
  event handlers; the app is client-side by design so this costs nothing.
- **Gemini forced by default, offline by explicit switch.** Every task in `src/ai/tasks.ts` has
  a handler in `src/ai/offline.ts`, used only when `VITE_AI_OFFLINE=true` is set in `.env`.
  Otherwise a missing key or model failure is returned as an error and the client throws, so the
  store fails the batch loudly (`Batch failed`) instead of degrading silently. The key is read
  only in `src/ai/server.ts` and `src/lib/serverFns/gemini.ts`. A flow awaits the AI promise but
  the engine tick is not stopped by a single call (offline it is effectively instant).
- **Vision from the same WebGL scene.** `src/render/capture.ts` renders to an offscreen renderer
  separate from the viewer canvas, so what Gemini sees matches what the operator sees.
- **Injectable ports keep `src/sim` pure.** `AiTaskFn` (`src/ai/tasks.ts`) and `CapturePort`
  (`src/sim/ports.ts`) are injected by the runner/store, so the simulation has no three/React/SDK
  imports (enforced by a Biome rule) and is fully testable with `offlineAi` + `truthCapture`.
- **Safety rules live in code, not the model.** `wait_for_removal` is only offered when a person
  is present; the adversarial judge decides pass/fail from metrics; KPI numbers are computed in TS.

## Known shortcuts

- The scripted engine (the dashboard stand-in until the three.js backend lands) queues cars with a
  center-to-center 6 m gap along the follower's own heading, measured only against cars that have
  an active mission or stand still in the lane; pull-over points (yard slots, rework bay) are
  ignored. It does not model the lane polyline corridor or a proportional rolling gap yet
  (TEAM_PLAN M4.1). See `src/lib/marshal/testing/scriptedSystem.ts`.
- AI latency pauses the tick (the runner awaits per car). Offline this is negligible; upgrade by
  running car AI in parallel if a live key makes batches visibly stall.
- Headless batches auto-decide an open assistance case after 90 simulated seconds so an
  unattended batch never hangs; the UI can decide earlier.
- `three@0.186` has no bundled types, so `src/render/three.d.ts` is a loose local shim.
