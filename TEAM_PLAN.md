# Marshal Three.js — Team Plan

> **Stack:** TanStack Start · TypeScript · Three.js · @google/genai  
> **Goal:** Full browser-native replacement for the CARLA/Python simulator — no Python, no GPU VM.  
> **Reference UI:** `Main.dc.html` (dark industrial dashboard, IBM Plex Sans/Mono, Space Grotesk)

---

## Architecture Overview

```
┌──────────────────── TanStack Start (Full-Stack TypeScript) ────────────────────┐
│                                                                                 │
│  ┌────────────────────── Client (Browser) ──────────────────────────────────┐  │
│  │                                                                          │  │
│  │  [React Dashboard UI]   ◄──────►  [Marshal Orchestrator (TS)]           │  │
│  │   TanStack Router / Store          flows: gate, lamp, route, yard,       │  │
│  │   IBM Plex · Space Grotesk          assist, parking                      │  │
│  │                                              │                           │  │
│  │                               In-Memory Event Bus (EventEmitter)         │  │
│  │                                              ▼                           │  │
│  │                                [Three.js Plant Engine]                   │  │
│  │                                 world · cars · cameras · kinematics      │  │
│  │                                 WebGLRenderTarget per camera             │  │
│  └──────────────────────────────────────────────┬───────────────────────────┘  │
│                                                 │ RPC (createServerFn / HTTP)  │
│  ┌────────────────────── Server (Node / Bun) ───▼───────────────────────────┐  │
│  │  TanStack Server Functions  (GEMINI_API_KEY stays server-side)           │  │
│  │   evaluateGatePhotos()        ──► @google/genai                          │  │
│  │   evaluateLampEvidence()      ──► @google/genai                          │  │
│  │   chooseRouteHazard()         ──► @google/genai                          │  │
│  │   analyzeAssistClip()         ──► @google/genai                          │  │
│  │   planParkingStep()           ──► A* Planner + @google/genai             │  │
│  │   replanYardSlots()           ──► @google/genai                          │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Team Split

Two people. Clear ownership per layer, shared on integration milestones (M3, M5, M6, M7).

| Person | Primary Ownership | Shared / Reviews |
|--------|-------------------|-----------------|
| **Faris** | Three.js simulator — world, roads, cars, driving AI, cameras, routine R-12 | Wires events into orchestrator; reviews server fn contracts |
| **Qais** | Marshal orchestrator flows, all TanStack Server Functions (Gemini), Dashboard UI (React + TanStack Router/Store) | Defines the TypeScript contract (`types.ts`); reviews simulator output format |

### Collaboration Points
- **`src/lib/marshal/types.ts`** — owned by **Qais**, both must agree before M2 starts
- **Gate flow (M3)** — Faris ships `GateCamera.capture()`; Qais ships `evaluateGatePhotos()` and wires the flow; integrate together
- **Lamp check (M5)** — same pattern: Faris ships evidence camera + routine; Qais ships `evaluateLampEvidence()` + lamp flow
- **Assist (M6)** — Faris ships clip capture + obstacle detection; Qais ships `analyzeAssistClip()` + dashboard action cards
- **Parking (M7)** — Faris ships bicycle model control loop; Qais ships A* + `planParkingStep()` + parking flow
- **Dashboard (M8)** — **Qais** owns all components; Faris connects `SimCanvas.tsx` for the Three.js canvas embed

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | **TanStack Start** (Vinxi + Nitro) |
| Language | **TypeScript** (strict mode, no Python) |
| 3D Engine | **Three.js** (`three` + `@types/three`) |
| State | **TanStack Store** (client) |
| Routing | **TanStack Router** |
| Server RPC | **`createServerFn`** (TanStack Start) |
| AI | **`@google/genai`** (Gemini 2.5 Flash) |
| Styling | Inline CSS matching `Main.dc.html` (IBM Plex Sans/Mono, Space Grotesk) |
| Asset models | Low-poly glTF (Kenney, Quaternius, Poly Pizza CC0) |
| Storage | IndexedDB (local frames) via `idb` library |

---

## Repository File Structure

```
src/
├── routes/
│   ├── __root.tsx            # TanStack Router root layout
│   ├── index.tsx             # Main dashboard page
│   └── simulator.tsx         # Three.js canvas full-screen route
│
├── lib/
│   ├── marshal/
│   │   ├── types.ts          # Shared message & event types (the Contract)
│   │   ├── orchestrator.ts   # Marshal state machine & flow runner
│   │   ├── flows/
│   │   │   ├── gate.ts       # Gate check flow
│   │   │   ├── lamp.ts       # Lamp check flow
│   │   │   ├── route.ts      # Route choice flow
│   │   │   ├── parking.ts    # Gemini parking flow
│   │   │   ├── yard.ts       # Yard re-plan flow
│   │   │   └── assist.ts     # Blocked-car assist flow
│   │   └── eventBus.ts       # In-process event emitter
│   │
│   ├── simulator/
│   │   ├── engine.ts         # ThreeJsBackend class (implements DrivingSystem)
│   │   ├── world/
│   │   │   ├── plantMap.ts   # Named points, road geometry, lane graph
│   │   │   ├── roadBuilder.ts# Procedural lane polylines from road table
│   │   │   └── dress.ts      # Fence, yard cars, trucks, props, gate boards
│   │   ├── actors/
│   │   │   ├── car.ts        # Car mesh + lights + fault system
│   │   │   ├── driver.ts     # Autonomous driver (pure-pursuit, queuing, pull-over)
│   │   │   ├── routine.ts    # R-12 test-track routine steps
│   │   │   └── props.ts      # Box, worker, cone, barrel, truck meshes
│   │   ├── cameras/
│   │   │   ├── gateCamera.ts # 4 fixed cameras around inspection_gate
│   │   │   └── carCamera.ts  # On-board: assist (chase), evidence front/rear
│   │   └── coords.ts         # CARLA ↔ Three.js coordinate conversion
│   │
│   └── serverFns/
│       ├── gate.ts           # evaluateGatePhotos()
│       ├── lamp.ts           # evaluateLampEvidence()
│       ├── route.ts          # chooseRouteHazard()
│       ├── assist.ts         # analyzeAssistClip()
│       ├── parking.ts        # planParkingStep() — A* + Gemini
│       └── yard.ts           # replanYardSlots()
│
├── components/
│   ├── Dashboard.tsx         # Root layout matching Main.dc.html
│   ├── Header.tsx            # Clock, Play/Pause, shift info
│   ├── KpiBar.tsx            # 6-tile KPI row
│   ├── GateLanes.tsx         # "Gate and lanes" section — chips per car
│   ├── Yard.tsx              # 8×10 slot grid (cell classes: empty/parked/fresh/res/alert)
│   ├── Dock.tsx              # Truck cards (truck / loading / waiting / left)
│   ├── CarDetail.tsx         # Per-car decision feed (click from gate/yard)
│   ├── WaitingOnYou.tsx      # Assist cards with image + options
│   ├── DecisionFeed.tsx      # Scrollable log (Everything / Exceptions toggle)
│   └── SimCanvas.tsx         # Three.js canvas (chase + top-down overlay)
│
└── app.tsx                   # TanStack Start entry point
```

---

## The Contract — TypeScript Interfaces

> **File:** `src/lib/marshal/types.ts`  
> All messages cross this boundary. Both simulator and orchestrator depend only on this file.

### Orchestrator → Simulator (Commands)

```ts
// src/lib/marshal/types.ts

export type MissionGoCmd = {
  op: 'mission.go'
  mission_id: string
  vin: string
  waypoints: string[]            // named map points
  max_speed_kmh?: number         // default 10
  routine?: string               // e.g. "R-12"
  routine_steps?: number[]       // subset of steps, e.g. [1]
}

export type MissionHoldCmd = {
  op: 'mission.hold'
  vin: string
  duration_s?: number
  reason: string
}

export type MissionResumeCmd = { op: 'mission.resume'; vin: string }

export type AssistChooseCmd = {
  op: 'assist.choose'
  case_id: string
  vin: string
  option: 'wait_for_removal' | 'pass_left_3kmh' | 'reroute_lane_c'
  chosen_by: 'supervisor' | 'gemini'
}

export type SimCommand = MissionGoCmd | MissionHoldCmd | MissionResumeCmd | AssistChooseCmd
```

### Simulator → Orchestrator (Events)

```ts
export type StatusEvent = {
  event: 'status'
  vin: string
  location: string         // last named map point passed
  x: number; y: number
  speed_kmh: number
  mission_id: string
  progress: number         // 0–1
  held: boolean
  stopped_for_assist: boolean
}

export type MissionDoneEvent  = { event: 'mission.done';  mission_id: string; vin: string; location: string }
export type TrackStepEvent    = { event: 'track.step';    vin: string; mission_id: string; routine: string; step: number; name: string; telemetry: Record<string, unknown> }
export type AssistRequestEvent = {
  event: 'assist.request'
  case_id: string
  vin: string
  location: string
  reason: string
  options: AssistOption[]
  clip_frames: string[]   // base64 JPEG[], 8 frames
}

export type SimEvent = StatusEvent | MissionDoneEvent | TrackStepEvent | AssistRequestEvent
export type AssistOption = 'wait_for_removal' | 'pass_left_3kmh' | 'reroute_lane_c'
```

### DrivingSystem Interface (the engine must implement this)

```ts
// src/lib/marshal/types.ts

export interface DrivingSystem {
  // Commands
  send(cmd: SimCommand): void

  // Events
  on(event: 'status',         cb: (e: StatusEvent) => void): void
  on(event: 'mission.done',   cb: (e: MissionDoneEvent) => void): void
  on(event: 'track.step',     cb: (e: TrackStepEvent) => void): void
  on(event: 'assist.request', cb: (e: AssistRequestEvent) => void): void

  // Direct engine calls (flows + dashboard)
  spawn(vin: string, opts: { at: string; colour: [number,number,number]; behind_m?: number }): void
  despawn(vin: string): void
  setFault(vin: string, faults: Set<'headlights' | 'brake_lights' | 'indicators'>): void
  repair(vin: string, defects: string[]): void
  getPositionXY(vin: string): { x: number; y: number; yaw: number }
  getTransform(point: string): { x: number; y: number; yaw: number }
  getAssistMedia(case_id: string): string[]      // base64 JPEG[]
  raiseAssist(vin: string, case_id: string, reason: string, options: AssistOption[]): void
  spawnProp(id: string, kind: 'box'|'worker'|'cone'|'barrel'|'truck', x: number, y: number, yaw: number): void
  removeProp(id: string): void
  storeArtifact(uri: string, blob: Blob): Promise<void>

  // State records (read-only from orchestrator)
  readonly passes: Record<string, number>           // vin → km/h at gate
  readonly assistOutcomes: Record<string, unknown>  // case_id → outcome
  readonly parkingLog: Record<string, unknown[]>    // vin → session log
}
```

---

## Sprint Milestones

### Milestone 1 — Static Plant World
**Owner:** 🟦 Faris  
**Done when:** Screenshot from each gate camera pose looks correct.

#### Tasks
- [ ] **M1.1** Set up TanStack Start project with TypeScript, Three.js, `@google/genai`
- [ ] **M1.2** Implement coordinate conversion (`src/lib/simulator/coords.ts`)
  - CARLA frame → Three.js: `mesh.position.set(x, z, y); mesh.rotation.y = -degToRad(yaw)`
- [ ] **M1.3** Build road geometry (`roadBuilder.ts`)
  - 8 roads (4 straights + 4 quarter-bends, r=22m), Lane C connector
  - Lane −2 centre-line polylines sampled every 1m
- [ ] **M1.4** Build named point map (`plantMap.ts`)
  - All 20+ named points from the map YAML (line_end → loading_bay)
  - Segment graph for route validation
- [ ] **M1.5** Dress the plant (`dress.ts`)
  - Perimeter fence (barrier every 4m)
  - Yard parked cars (road 7 lane −5, every 6m)
  - Loading trucks (road 5 inner apron, every 13m)
  - Storage compound rows (inner loop)
  - Gate calibration boards, cones, warning board
  - Rework bay props: barrels, box, iron plank, sign
- [ ] **M1.6** Car model with working lights
  - Emissive materials for: LowBeam (front pair), Brake (rear bar), Reverse (rear white), LeftBlinker, RightBlinker
  - `setFault('headlights')` → lights off visually, telemetry still reports ON
- [ ] **M1.7** Sun / ambient lighting — low sun angle (15°) for dusk scene

---

### Milestone 2 — Lane Graph + One Car Driving
**Owner:** 🟦 Faris  
**Done when:** `line_end → inspection_gate → test_track → report_point` at 10 km/h, crosses gate at ~5 km/h.

#### Tasks
- [ ] **M2.1** Lane graph pathfinding (A* or Dijkstra over the segment graph)
- [ ] **M2.2** Pure-pursuit steering controller
  - Lookahead: ~2× car length
  - Max front-wheel angle: 30°
  - Kinematic bicycle model: wheelbase 2.85m
- [ ] **M2.3** Speed controller (gentle accel/brake to `max_speed_kmh`)
- [ ] **M2.4** Arrival detection
  - **Normal:** within 3–4m of waypoint
  - **Precise stop** (`inspection_gate`, `pull_over` points): ±0.4m, creep at walking pace, 30s timeout
  - **Drive-through** (`inspection_gate`): don't stop, record `passes[vin] = km/h`, emit `status`
- [ ] **M2.5** Implement `status` event at 1Hz and `mission.done` event
- [ ] **M2.6** Implement `send(MissionGoCmd)` dispatch in `engine.ts`

---

### Milestone 3 — Gate Cameras + Server Functions (Gate Flow End-to-End)
**Owner:** 🟦 Faris (cameras) · 🟧 Qais (server fn + flow wiring) — integrate together  
**Done when:** Gate check flow runs: car drives through, 4 photos captured, Gemini evaluates, pass/fail reported.

#### Simulator Tasks
- [ ] **M3.1** `GateCamera` class — 4 `WebGLRenderTarget` cameras at `inspection_gate`
  ```
  front:  fwd +7m, z 1.5m, yaw 180°, pitch −5°, FOV 90°
  rear:   fwd −7m, z 1.5m, yaw   0°, pitch −5°, FOV 90°
  left:   right −5m, z 1.2m, yaw +90°, pitch −5°, FOV 90°
  right:  right +5m, z 1.2m, yaw −90°, pitch −5°, FOV 90°
  ```
  - Capture on drive-through trigger (car centre crosses gate mark)
  - Render at 1280×720, export as base64 JPEG (no overlays)
- [ ] **M3.2** `GateCamera.capture(vin, views)` returns `{ front, rear, left, right }` as base64 strings

#### Server Function Tasks
- [x] **M3.3** `evaluateGatePhotos(frames: GateFrames): Promise<GateResult>` (`src/lib/serverFns/gate.ts`)
  - Calls Gemini with all 4 frames
  - Checks: car colour vs build sheet, body damage confidence
  - Returns: `{ pass: boolean; colour: string; confidence: number; faults: string[] }`
- [x] **M3.4** Wire gate flow in `src/lib/marshal/flows/gate.ts`
  - On `mission.done` at `inspection_gate`: capture → server fn → update orchestrator state → emit next mission

_M3.3/M3.4: the gate flow runs on the ScriptedDrivingSystem until Faris's ThreeJsBackend lands; while gate frames are placeholders the image check stays offline (deterministic), not sent to Gemini._

---

### Milestone 4 — Multi-Car: Queuing, Pull-over, Yard Slots
**Owner:** 🟦 Faris  
**Done when:** 4-car shift ends with each car in its slot, no deadlock.

#### Tasks
- [ ] **M4.1** Queuing behaviour
  - Gap: 6m standing, proportional when moving
  - Waiting behind a **plant car** = queuing (must NOT trigger assist request)
  - Ignore cars on shoulder (pull_over points) — they're not in the lane
- [ ] **M4.2** Pull-over sequence
  1. Route to driving lane 12m before spot (`PULL_OVER_RUN_M = 12`)
  2. Steer toward point 4m beyond spot (`PULL_OVER_AIM_M = 4`), walking pace
  3. Within 3m → `_squareUp()`: set exact pose, zero yaw offset
- [ ] **M4.3** Leaving a slot: start from lane beside slot, 5m ahead (`PULL_OUT_M = 5`)
- [ ] **M4.4** Slot-to-slot yard re-plan moves
  - Forward if new slot is ahead, reverse if behind (≤30m)
  - Or: reverse down lane, turn in 7m before slot
  - Wait if a car is between the two slots
- [ ] **M4.5** `spawn(vin, { at, colour, behind_m })` — queue cars 9m apart behind `line_end`
- [ ] **M4.6** `despawn(vin)` — remove car + all attached cameras from scene

---

### Milestone 5 — Routine R-12 + Lamp Check + Faults
**Owner:** 🟦 Faris (routine + cameras + fault system) · 🟧 Qais (lamp server fn + lamp flow)  
**Done when:** VIN-DARK fails headlights step, VIN-OK passes.

#### Simulator Tasks
- [ ] **M5.1** On-board camera setup (`carCamera.ts`)
  ```
  assist (chase): x −6m, z 3m, pitch −12°, FOV 90°, buffer 10s / 8 frames
  evidence rear:  x −7m, z 1.6m, pitch −5°, yaw 0, FOV 38°, buffer 4s / 8 frames
  evidence front: x +7.5m, z 1.6m, pitch −5°, yaw 180°, FOV 38°, buffer 4s / 8 frames
  ```
  - Tick: every 0.2 simulated seconds
  - Render: `WebGLRenderTarget` → `readRenderTargetPixels` → `OffscreenCanvas` → JPEG blob (quality 0.9)
  - Keep rolling buffer of last N blobs per camera (no overlays on evidence frames)
- [ ] **M5.2** Routine R-12 steps (`routine.ts`)

  | # | Name | Action | Lights | Duration | Telemetry |
  |---|------|---------|--------|----------|-----------|
  | 1 | headlights | stand braked | LowBeam | 8s | `{headlights:"ON"}` |
  | 2 | indicators | alt L↔R every 1.2s | LeftBlinker ↔ RightBlinker | 10s | `{indicator_left:"BLINK",...}` |
  | 3 | brake_lights | stand braked | Brake | 8s | `{brake_signal:true,brake_lamps:"ON"}` |
  | 4 | reverse | reverse 3m ≤4km/h | Reverse | ≤25s | `{gear:"R",distance_m:3.02,lateral_m:0.05}` |
  | 5 | brake_test | accel to 20km/h, full brake | — | ≤ done | `{start_speed_kmh,stopping_distance_m}` |
  | 6 | full_lock | L90° R360° L270° at 5km/h | — | ≤90s | measured radii |

  - Emit `track.step` after each step
  - Telemetry always "healthy" — fault car still reports ON
- [ ] **M5.3** `setFault(vin, Set(['headlights']))` → headlights don't render, telemetry unaffected
- [ ] **M5.4** `repair(vin, defects)` → clears fault, lights work again

#### Server Function Tasks
- [x] **M5.5** `evaluateLampEvidence(frames: string[], step: string): Promise<LampResult>` (`src/lib/serverFns/lamp.ts`)
  - Returns: `{ expected: string; observed: string; pass: boolean }`
- [x] **M5.6** Wire lamp flow (`src/lib/marshal/flows/lamp.ts`)
  - On `track.step`: if step 1 (headlights) → call `evaluateLampEvidence` with front evidence frames
  - Telemetry ON + camera OFF → FAIL → `mission.go` to `rework.electrical`

_M5.5/M5.6: runs on the ScriptedDrivingSystem; the lamp check stays offline while evidence frames are placeholders (no real JPEGs until M5.1)._

---

### Milestone 6 — Props, Obstacle Detection, Assist Flow (Scene C)
**Owner:** 🟦 Faris (obstacle detection + clip buffer) · 🟧 Qais (analyzeAssistClip server fn + dashboard action cards)  
**Done when:** Scene C works from the dashboard (car stops, clip shown, supervisor picks wait, lane clears, car resumes).

#### Simulator Tasks
- [ ] **M6.1** `spawnProp(id, kind, x, y, yaw)` + `removeProp(id)` for box, worker, cone, barrel, truck
- [ ] **M6.2** Obstacle detection in front of car
  - Scan: `9m + v²/(2·4)` ahead, ±1.8m lateral of centreline
  - Stop for props and walkers; vehicles handled by queuing
  - Parked plant cars (on shoulder) are NOT obstacles
- [ ] **M6.3** Blocked detection
  - Speed < 0.6 km/h for 5s with non-plant obstacle within 14m ahead, ±3m lateral
  - Raise `assist.request`:
    - `case_id = "AC-<vin>-<n>"`
    - `reason`: describe obstacle
    - `options`: subset of `['wait_for_removal', 'pass_left_3kmh', 'reroute_lane_c']`
    - `clip_frames`: last 10s, 8 frames from assist camera
- [ ] **M6.4** Assist option execution on `AssistChooseCmd`:
  - **`wait_for_removal`:** hold until lane clear, record seconds waited
  - **`pass_left_3kmh`:** lane-change left 7m, pass at 3km/h, return after 6m clear, measure min distance + contacts
  - **`reroute_lane_c`:** reroute via lane C at mission speed
- [ ] **M6.5** `getAssistMedia(case_id)` returns the stored clip frames (base64 JPEG[])

#### Server Function Tasks
- [x] **M6.6** `analyzeAssistClip(frames: string[]): Promise<AssistAnalysis>` (`src/lib/serverFns/assist.ts`)
  - Returns Gemini's scene description + bounding boxes (for dashboard overlay)
  - `{ description: string; boxes: BoundingBox[]; recommendation: AssistOption }`

_M6.6: clip analysis stays offline while the scripted engine's clip frames are placeholders._

---

### Milestone 7 — Gemini Parking (Bicycle Model)
**Owner:** 🟦 Faris (bicycle model control loop + obstacle box reporting) · 🟧 Qais (A* planner + planParkingStep server fn + parking flow)  
**Done when:** Gemini parks a car in Y7 behind a parked car in Y8.

#### Simulator Tasks
- [ ] **M7.1** Per-tick `control` command execution
  ```ts
  // Kinematic bicycle model per tick dt
  // wheel ±1 = ±30° front-wheel angle; positive = left in slot frame
  x   += v * Math.cos(yaw) * dt
  y   += v * Math.sin(yaw) * dt
  yaw += (v / L) * Math.tan(delta) * dt  // L = 2.85m
  ```
- [ ] **M7.2** Obstacle box reporting per tick
  - Every vehicle, walker, prop within 15m of parking slot
  - As: `{ id, x, y, yaw, length, width, moving: boolean }`
  - Ignore props flatter than 0.15m
- [ ] **M7.3** Safety stop: cancel move if next 0.4m brings car within 0.2m of any obstacle
- [ ] **M7.4** Record `parkingLog[vin]` per session (decisions, outcome, final pose)

#### Server Function Tasks
- [x] **M7.5** `planParkingStep(pose, obstacles, slot): Promise<ParkingStep>` (`src/lib/serverFns/parking.ts`)
  - A* plans up to 3 paths in slot frame
  - Gemini picks a path or says "parked" or "ask a person"
  - Returns: `{ path: ControlSequence; decision: string; confidence: number }`

_M7.5: runs against the ScriptedDrivingSystem's bicycle model until M7.1–M7.4 land on the three.js engine; parking is not image based, so it calls Gemini when configured._

---

### Milestone 8 — Dashboard UI
**Owner:** 🟧 Qais (all React components) · 🟦 Faris (`SimCanvas.tsx` Three.js canvas embed)  
**Done when:** All sections live-update from simulator state, matches `Main.dc.html` visual design.

#### Design Tokens (from `Main.dc.html`)

```ts
// src/lib/designTokens.ts
export const colors = {
  bg:       '#12171D',
  surface:  '#1B222B',
  surfaceAlt:'#151B22',
  border:   '#2A3340',
  borderAlt:'#344052',
  text:     '#E9E6DE',
  textMuted:'#A9B1BC',
  textDim:  '#8C95A1',
  accent:   '#E39B2E',   // amber — alert / CTA
  blue:     '#7FA7FF',   // gate chips, links
  blueLight:'#B8CCFF',   // lane chips, just-parked cells
  sand:     '#D9D5CB',   // parked cells
  gold:     '#F6C77A',   // KPI highlight
  rework:   '#C2621F',   // rework chips
}
export const fonts = {
  ui:   "'IBM Plex Sans', sans-serif",
  mono: "'IBM Plex Mono', monospace",
  head: "'Space Grotesk', sans-serif",
}
```

#### Component Tasks

- [x] **M8.1** `Header.tsx` — shift info, clock (IBM Plex Mono 26px), Play/Pause/Next/Restart buttons, "Pause when needed" toggle
- [x] **M8.2** Progress bar (amber, 6px, `transition: width 0.3s`)
- [x] **M8.3** `KpiBar.tsx` — 6 tiles: Off the line, Decisions made, Needed you, In the yard, Shipped, Rework
  - Value font: Space Grotesk 700 32px
- [x] **M8.4** `GateLanes.tsx` — 4 groups: At gate, Driving to slot, Waiting on you, Rework bay
  - Chips: `.chip.gate` (blue bg), `.chip.lane` (light blue), `.chip.alert` (amber + pulse), `.chip.rework` (orange)
  - Click chip → select car → show CarDetail
- [x] **M8.5** `Yard.tsx` — 8×10 slot grid
  - Row labels A–H (IBM Plex Mono 11px, muted)
  - Cell states: `.cell.empty`, `.cell.parked` (sand), `.cell.fresh` (light blue, just parked), `.cell.res` (dashed blue border, en route), `.cell.alert` (amber + pulse, needs you)
  - Selected: `.cell.sel` (white outline)
- [x] **M8.6** `Dock.tsx` — truck cards
  - States: `.truck` (default), `.truck.loading` (amber bg), `.truck.waiting` (amber + pulse), `.truck.left` (55% opacity)
- [x] **M8.7** `CarDetail.tsx` — per-car decision timeline
  - Tag classes: `.tag.GEMINI` (blue bg), `.tag.YARD` (dark amber), `.tag.SUPERVISOR` (light bg), `.tag.PLANT` (dark surface)
- [x] **M8.8** `WaitingOnYou.tsx` — action cards
  - Image (140px high, cover), body text, option buttons
  - Primary option: `.opt.rec` (amber bg, recommended)
  - Binds to `AssistRequestEvent` and `assist.choose` dispatch
- [x] **M8.9** `DecisionFeed.tsx` — scrollable log (520px, overflow-y auto)
  - Segment toggle: "Everything" / "Exceptions only" (`.seg` / `.seg.on`)
- [ ] **M8.10** `SimCanvas.tsx` — Three.js canvas overlay
  - Chase camera (main view)
  - Top-down mini-map inset (bottom-right)
  - Gemini note overlay (bottom-left): `"GEMINI · VIN-P1 parking Y8: path B (0.81)"`
  - Notes pushed via `{ op: 'note', text, kind }` from orchestrator

_M8.1–M8.9 live on `/shift`, driven by the scripted demo. GateLanes has a fifth group, "Held for the yard crew" (parking abandoned or yard full); the dock adds `.truck.late` for a truck running late._

---

### Milestone 9 — Route Choice + Yard Re-Plan Server Functions
**Owner:** 🟧 Qais  
**Done when:** Scenes A and D work end-to-end.

#### Tasks
- [x] **M9.1** `chooseRouteHazard(hazardDescription: string, options: RouteOption[]): Promise<RouteChoice>` (`src/lib/serverFns/route.ts`)
  - Used at `report_point`: lane C vs finishing corner
  - Returns chosen route + confidence
- [x] **M9.2** `replanYardSlots(currentLayout, constraints): Promise<YardReplan>` (`src/lib/serverFns/yard.ts`)
  - Triggered by late/early truck scenario
  - Returns list of slot moves: `[{ vin, from, to }]`
- [x] **M9.3** Wire `yard.ts` flow — validate re-plan, dispatch slot-to-slot missions
- [x] **M9.4** Hazard staging helper for Scene A
  - `stageHazard(at: 'finishing_corner', preset: 'forklift_unloading')` → spawns lorry, boxes, sign, cones, worker at correct offsets

_M9: Scenes A and D run end-to-end in the scripted `/shift` demo._

---

### Milestone 10 — Demo Scenes & Polish
**Done when:** All 5 scenes can be screen-recorded.

#### Tasks
- [ ] **M10.1** Scene A: 4-car shift — blue + 1 red car, route choice via Lane C, yard parking
- [ ] **M10.2** Scene B: VIN-DARK headlight fault → rework → repair → recheck → PASS
- [ ] **M10.3** Scene C: VIN-STUCK + box + worker → assist request → supervisor waits → lane clears
- [ ] **M10.4** Scene D: Late truck T-1 → yard re-plan → slot moves with P2 waiting for P1
- [x] **M10.5** Scene E: Gemini parking Y7 (car behind Y8)
- [x] **M10.6** Demo controls in UI: "Play shift", "Next exception", "Restart", speed slider
- [ ] **M10.7** `storeArtifact(uri, blob)` — save JPEG frames to IndexedDB via `idb`

_M10.1–M10.4 play in the scripted `/shift` demo; they stay open until screen-recorded on the three.js engine. M10.5 is in the scripted demo too: P001 parks in A7 (`yard.Y7`) behind P091 in A8 (`yard.Y8`)._

---

## Key Implementation Notes

### Coordinate Conversion (`coords.ts`)
```ts
// CARLA (left-handed, z-up) → Three.js (right-handed, y-up)
export function carlaToThree(x: number, y: number, z: number, yawDeg: number) {
  return {
    position: new THREE.Vector3(x, z, y),
    rotationY: -THREE.MathUtils.degToRad(yawDeg),
  }
}
// Named points are stored in CARLA frame; convert on load into Three.js scene
```

### Camera Frame Export
```ts
// No overlays on evidence frames — Gemini reads them raw
async function captureJpeg(renderTarget: THREE.WebGLRenderTarget, renderer: THREE.WebGLRenderer): Promise<string> {
  const canvas = new OffscreenCanvas(renderTarget.width, renderTarget.height)
  const ctx = canvas.getContext('2d')!
  const buf = new Uint8Array(renderTarget.width * renderTarget.height * 4)
  renderer.readRenderTargetPixels(renderTarget, 0, 0, renderTarget.width, renderTarget.height, buf)
  // flip Y (WebGL origin bottom-left)
  const imageData = ctx.createImageData(renderTarget.width, renderTarget.height)
  // ... flip rows ...
  ctx.putImageData(imageData, 0, 0)
  const blob = await canvas.convertToBlob({ type: 'image/jpeg', quality: 0.9 })
  return blobToBase64(blob)
}
```

### TanStack Server Function Pattern
```ts
// src/lib/serverFns/gate.ts
import { createServerFn } from '@tanstack/start'
import { GoogleGenAI } from '@google/genai'

export const evaluateGatePhotos = createServerFn({ method: 'POST' })
  .validator((data: GateFrames) => data)
  .handler(async ({ data }) => {
    const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY! })
    // ... call Gemini with base64 frames ...
    return result satisfies GateResult
  })
```

### Event Bus Pattern
```ts
// src/lib/marshal/eventBus.ts
import { EventEmitter } from 'eventemitter3'
export const bus = new EventEmitter<{
  'sim:status':         [StatusEvent]
  'sim:mission.done':   [MissionDoneEvent]
  'sim:track.step':     [TrackStepEvent]
  'sim:assist.request': [AssistRequestEvent]
  'marshal:command':    [SimCommand]
  'ui:note':            [{ text: string; kind: 'gemini' | 'plant' | 'yard' }]
}>()
```

### Plant Map Points Reference

| Point | x | y | yaw° | Notes |
|-------|---|---|------|-------|
| `line_end` | 8.0 | 5.25 | 0 | Cars queue 9m apart behind |
| `buffer_zone_b` | 18.0 | 5.25 | 0 | |
| `inspection_gate` | 32.0 | 5.25 | 0 | Drive-through at 5km/h |
| `test_track` | 62.0 | 5.25 | 0 | R-12 routine here |
| `report_point` | 100.0 | 5.25 | 0 | Route choice: corner vs Lane C |
| `lane_c` | 117.25 | −50.0 | −90 | |
| `finishing` | 90.0 | −109.25 | 180 | |
| `rework.electrical` | 150.75 | −40.0 | −90 | pull_over (lamp faults) |
| `yard.entry` | −27.25 | −80.0 | 90 | |
| `yard.Y1–Y8` | −30.75 | −68→−29.5 | 90 | pull_over, 5.5m apart |

### Car Dimensions (Critical for Parking Geometry)
- **Length × Width:** 4.9m × 1.9m
- **Wheelbase:** 2.85m
- **Max steer angle:** 30° (`STEER_DEG`)
- **Parking clearance:** 0.3m at sides, heading within 8° of slot

---

## Dependency Graph (Build Order)

```
M1 (Static World)
  └── M2 (One Car Driving)
        ├── M3 (Gate Flow)          ← needs M3 Server Fn (parallel)
        ├── M4 (Multi-Car)
        │     └── M5 (Routine R-12) ← needs M5 Server Fn (parallel)
        │           └── M6 (Assist) ← needs M6 Server Fn (parallel)
        │                 └── M7 (Parking) ← needs M7 Server Fn (parallel)
        │                       └── M9 (Route + Yard)
        │                             └── M10 (Demo Scenes)
        └── M8 (Dashboard UI)       ← can start after M1, wires in progressively
```

---

## Definition of Done (per PR)

1. TypeScript compiles with `--strict`, zero errors
2. All new public functions have JSDoc with parameter and return types
3. No Python files added
4. Server functions use `createServerFn` — no raw `fetch` to Gemini from client
5. Evidence camera frames have zero overlays / debug text
6. Named-point coordinates match the CARLA map YAML values exactly
7. Existing `Main.dc.html` design tokens honoured (colours, fonts, spacing)

