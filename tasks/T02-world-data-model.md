# T02 — World & Data Model

**Fase:** 1
**Depends on:** T01
**Estimasi:** 1 hari

## Tujuan

Membuat peta plant (titik, zona, segmen) dan seluruh model data batch/flow sebagai tipe
TypeScript + skema zod, plus RNG deterministik. Ini fondasi semua task lain dan **salinan
mandiri** — bukan impor dari project lain.

## File yang disentuh

- `src/sim/world.ts` (baru) — peta + routing
- `src/sim/records.ts` (baru) — record & enum + skema zod
- `src/sim/rng.ts` (baru) — RNG seeded
- `src/sim/stages.ts` (baru) — mapping state → flow
- `src/sim/__tests__/world.test.ts`, `records.test.ts`, `rng.test.ts` (baru)

## Spec

### Peta (`world.ts`)

Titik 2D meter (top-down; viewer memetakan `x → three.x`, `y → three.z`):

```ts
type Point = { name: string; zone: Zone; x: number; y: number };
type Segment = { from: string; to: string; oneWay?: boolean };
```

Layout wajib ada (koordinat bebas, ±250×80 m):

- `line_end` → `inspection_gate` → `test_track` → `report_point`
- `report_point` → `finishing_corner.p1..p3` → `finishing` → `yard.entry` (rute A)
- `report_point` → `lane_c` → `finishing` (rute B, lebih panjang)
- `report_point` → `rework.paint|body|electrical|mechanical` (one-way) → kembali ke `inspection_gate`
- `yard.entry` → `yard.Y1..Y8` + `loading_bay`
- `line_end` → `buffer_zone_b`

Fungsi: `route(from, to): string[]` (Dijkstra/BFS terpendek), `routeLength(points)`, `point(name)`.

### Records (`records.ts`)

Skema zod + tipe turunan untuk minimal:

- `Car` — `vin, buildSheet{model,colour,options}, state, location, slot?, missionId?, route?`
- `CarState` — `line_end, waiting_dispatch, held, inspection_gate, test_track, report, rework,
  yard, shipped`
- `Mission` — `id, vin, waypoints[], status, routine?, routineSteps?`
- `Event` — `seq, at, flow? (1-7), source, title, detail, level(info|good|warn|bad), payload?`
- `AssistanceCase` — `caseId, vin, situation, options[], recommended, risk, peopleOrEquipment,
  status, choice?, decidedBy?, openedAt, resolvedAt?`
- `InspectionResult` — `vin, kind(visual|functional), verdict(PASS|REVIEW|FAIL), findings[],
  mismatches[], confidence`
- `CarReport` — `vin, verdict, summary, findings[], failedChecks[], rework?, shipped`
- `Proposal` — `id, kind(replan|rework|review), summary, payload, status, decidedBy?`
- `Batch` — `batchId, status, requestedCars, createdCars, finishedCars, progress, seed, engine,
  incident{enabled,vin?,situation?}, manifest, counts, report?, error?`
- `BatchCar` — `vin, expected{kind, faultStep?}, state, stage(1-7), location, slot?, gateVerdict?,
  reportVerdict?`
- `BatchEvent` = `Event` + `batchId`
- `Scenario`, `TestRun` (untuk flow 6-7) — lihat T11/T12 untuk bentuk final

Label yang digenerate (dipakai T13): `clean | wrong_colour | dead_lamp | incident`.

### RNG (`rng.ts`)

- `makeRng(seed: number)` → `{ next(), int(max), pick(arr), sample(n), bool(p) }`
  memakai mulberry32 (atau xorshift32).
- Semua keacakan simulasi, manifest, insiden, dan adversarial **wajib** lewat RNG ini.

### Stages (`stages.ts`)

- `stageFor(state, hasOpenCase): number` — `line_end→1, inspection_gate|review→3,
  test_track→4, report|rework|yard|shipped→5`, kasus terbuka → 2.
- `isFinished(state): boolean` — `yard|shipped|rework` (rework hanya setelah tiba di bay).

## Acceptance criteria

- [ ] `route("line_end", "yard.Y1")` mengembalikan rute A (lebih pendek) dan
      `route("report_point", "yard.Y1")` konsisten dengan graf segmen.
- [ ] Semua skema zod `parse()` round-trip untuk satu objek contoh tiap tipe.
- [ ] `makeRng(7)` deterministik: dua instance menghasilkan urutan identik (unit test).
- [ ] Tidak ada satu pun dependensi ke three/React di `src/sim/` (dijaga lint import).

## Verifikasi

```bash
bun run test
bun run check:standalone
```

## Catatan

- Jangan menambah fitur peta di luar daftar titik di atas; T04–T09 bergantung pada nama-nama ini.
- Simpan peta sebagai objek TS terketik, bukan YAML file — cukup untuk standalone, tanpa parser tambahan.
