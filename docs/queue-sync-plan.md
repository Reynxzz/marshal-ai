# Queue sync fix — hackathon-night plan

> Note (later): the dashboard now lives on `/`; the `/shift` route was removed, so read `/shift`
> below as that same dashboard.

**Problem.** After merging Qais's branch (PR #1, `1f91c21`), the `/shift` 3D view looks like the
line is not synchronized: cars drive through each other and stack on the same coordinates instead
of queuing. The demo is tonight; this document scopes the minimum fix and compares it against the
other documents that still need finishing and against what is actually implemented in `/` and
`/shift` today.

---

## 1. Current implementation, `/` vs `/shift`

The merged app carries **two independent simulation stacks**. They share only the three.js
dependency and `src/render/cars.ts`.

| | `/` (batch dashboard) | `/shift` (live shift dashboard) |
|---|---|---|
| Route | `src/routes/index.tsx` | `src/routes/shift.tsx` |
| Sim engine | `src/sim/engine.ts` (`SimEngine`, fixed-step, kinematic) | `ScriptedDrivingSystem` (`src/lib/marshal/testing/scriptedSystem.ts`) |
| Plant map | `src/sim/world.ts` (standalone coords, x 10–222, y 4–79) | `src/lib/marshal/points.ts` (TEAM_PLAN CARLA frame, y down to −109) |
| Orchestration | `src/sim/batch/orchestrator.ts` (batch state machine) | `src/lib/marshal/orchestrator.ts` + flows |
| 3D view | `src/render/scene.ts` + `src/ui/YardScene.tsx` | `src/lib/simview/scene.ts` + `src/components/sim/SimCanvas.tsx` |
| Store | `src/stores/batch.ts` | `src/stores/shift.ts` |
| AI | `src/ai/*` (7 flows, online or offline switch) | `src/lib/serverFns/*` (Gemini, offline fallback) |
| Status | T01–T18 complete, 232 tests green | Scenes A–E run on the scripted engine, tests green |
| Queue behavior | not affected by the merge | **queue collapses when a car stops** |

Why `/shift`'s line collapses (reproduced with `ScriptedDrivingSystem` + orchestrator, 5 cars):
`drive()` (`src/lib/marshal/testing/scriptedSystem.ts:540`) moves every car straight to the same
waypoint at the same speed with no gap control and no collision. Cars spawn 9 m apart
(`orchestrator.ts:336`), but when the leader stops (R-12 routine on the test track, gate AI check,
assist blocker) the followers keep driving:

- P001–P005 all reach `(62.0, 5.25)` (test track) from ≈ t+24 s; every pair ends up < 1 m apart;
- the stacking repeats at every stop for the rest of the shift.

`/` is untouched by the merge (only `src/render/three.d.ts` type stubs and a nav link changed), so
any behavior difference you see is on `/shift` only.

---

## 2. Compared to the other documents that still need finishing

| Document | What it is | Status | What tonight's plan does with it |
|---|---|---|---|
| `FARIS_TASKS.md` | Faris's simulator rebuild: M1–M10, `src/lib/simulator/*`, **ThreeJsBackend implementing `DrivingSystem`** | 60+ boxes unchecked, `src/lib/simulator/` does not exist | Only the M2.4/M4.1 *behavior* (queue gap, follow-the-leader) is ported into the scripted engine as a stopgap. Nothing else. |
| `TEAM_PLAN.md` | Shared contract + milestones; M4.1 queuing owned by Faris; M3/M5/M6/M7 notes say flows run on the ScriptedDrivingSystem "until Faris's ThreeJsBackend lands" | mixed (`[x]` on Qais's flows, no engine) | Tonight is exactly that interim state; this fix makes the stand-in behave per M4.1 (6 m standing gap, do not trigger assist when queuing). |
| `tasks/README.md` (T01–T18) | The `/` batch product (sim engine, 7 flows, report) | implemented and green | Do not touch. `src/sim/world.ts` coordinates are intentionally standalone. |
| `docs/ARCHITECTURE.md` | Architecture of the merged app | current | Add one "Known shortcuts" line about the queue stopgap when it lands. |
| `docs/DEMO.md` | 5-minute `/` demo script | current | Not the tonight path; the `/shift` walkthrough is the new demo surface. |

Parts of `FARIS_TASKS.md` are already obsolete in the merged repo and should be pruned before it
is worked seriously: M1.6 car model/lights exists (`src/render/cars.ts`), M1.7 lighting exists in
both scenes, M8.10 `SimCanvas.tsx` exists and is tested (`src/components/sim/`). Finishing that
document as written would rebuild shipped code and add a third parallel tree next to
`src/lib/simview/` and `src/sim/`.

---

## 3. The fix (tonight, one file + one test)

### 3.1 Queue discipline in `drive()`

File: `src/lib/marshal/testing/scriptedSystem.ts`

- Constants: `QUEUE_GAP_M = 6`, `QUEUE_LATERAL_M = 2.5`, `QUEUE_LOOKAHEAD_M = 40`.
- Before moving, find the *leader*: nearest other car that
  1. has an active `mission` (pre-parked / already parked cars are ignored — mirrors M4.1
     "ignore cars on pull-over"),
  2. is ahead: positive projection on the car's heading,
  3. is within ±2.5 m lateral,
  4. is within 40 m.
- Cap this tick's movement so the center-to-center gap stays ≥ `QUEUE_GAP_M`
  (`remaining = min(remaining, aheadDistance − QUEUE_GAP_M)`, floor 0).
- Cars waiting in the queue never raise assist (`raiseAssist` is untouched; queuing behind a
  plant vehicle stays a plant event, not an assist case).
- Parking (`execute()`) is not touched — parked cars and parking maneuvers are unaffected.
- `shortcut:` comment in code: center-based gap, own-heading only; upgrade to TEAM_PLAN's lateral
  corridor + proportional rolling gap when the real ThreeJsBackend lands.

Deliberately **not** tonight: gate crawl at ~5 km/h (M2.4 drive-through). The gate flow already
waits for `mission.done` at `inspection_gate`; the queue fix is what makes the line behind it
read correctly. Cosmetic only.

### 3.2 Test

New: `src/lib/marshal/__tests__/queue.test.ts`

- Spawn `VIN-A` at `line_end`, `VIN-B` at `behind_m = 9`; both `mission.go` through
  `['buffer_zone_b', 'inspection_gate']`.
- `mission.hold` A for 5 s → assert B stops ≥ 6 m behind A and never overtakes.
- A resumes → assert B moves again and both missions complete.

### 3.3 Existing-test fallout (mechanical only)

- Run `bun run test`. Timing-sensitive suites may need larger sim budgets: `runUntil` defaults to
  600 s in `src/lib/marshal/__tests__/harness.ts:70` (Scene A two-car case in
  `orchestrator.test.ts`); `dashboard.test.tsx` "plays the default shift" already allows 2400 s.
- Rule: only widen budgets; never weaken assertions. If an assertion's meaning would change, stop
  and reassess instead of editing it.

### 3.4 Optional polish (only if green early)

Draw a lane strip west of `line_end` so the spawn queue is not standing on bare ground
(`src/lib/simview/scene.ts` / `plantMap.ts`, ~5 lines). Cosmetic.

### 3.5 Explicitly out of scope tonight

`FARIS_TASKS` backend, `ThreeJsBackend`, map unification (`world.ts` vs `points.ts`), camera
ports (M3/M5/M6/M7), `M8.10`. None of it is reachable before the deadline, all of it risks the
demo.

---

## 4. Verification

1. `bun run typecheck && bun run test` — all green.
2. `bun run dev` → `/shift` → Play, offline (`VITE_AI_OFFLINE=true`): queue holds behind the
   R-12 stop and behind the blocked car; "Next exception" still fast-forwards.
3. `bun run build` before freeze.
4. Rehearse the 2-minute demo twice; if pacing is slow, raise the speed control (default 4×).

**Fallback:** the diff is one engine file + one test file. If anything smells wrong during
rehearsal, `git revert` the commit and ship today's green state.

---

## 5. Tonight's timeline

| Time | Step |
|---|---|
| 0:00–0:40 | Queue rule in `scriptedSystem.ts` |
| 0:40–1:10 | New queue test; full suite; budget fixes if red |
| 1:10–1:30 | Browser rehearsal on `/shift`, offline |
| 1:30–2:00 | Optional lane strip; ARCHITECTURE.md shortcut line |
| then | Freeze, `bun run build`, deploy/submit |

---

## 6. Risks

- **False holds on crossing paths.** Bounded by active-mission + 2.5 m lateral + 40 m lookahead;
  worst case a brief pause. Accept; upgrade with the real backend.
- **Slower shifts** (cars now wait). Default 4× speed absorbs it; "Next exception" is unchanged.
- **Test fallout** is expected to be mechanical (budgets); if it is not, stop and reassess rather
  than editing assertions.
- **Do not refactor toward `src/lib/simulator/` tonight** — that is `FARIS_TASKS` scope, days of
  work, and it changes the `/shift` wiring contract.

## 7. Definition of done tonight

- `/shift` shows a queue: no two cars overlap within 6 m while one is stopped.
- 232+ tests green (new queue test added), typecheck green, build green.
- No change to `/`, `src/sim/`, `src/render/`, or `src/lib/simview/` (except the optional strip).
- One commit; revertible.
