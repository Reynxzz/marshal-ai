# Marshal Three.js — Task Index

> Status: **T01–T18 implemented** (flows 1–7, offline-first AI, dashboard, viewer, tests, docs).

Fitur: user memasukkan jumlah mobil di dashboard, sistem membuat satu batch, menjalankan
mobil-mobil itu melewati 7 flow di simulasi three.js, mencatat log, lalu Gemini (ADK)
memberi feedback dan report per batch.

## Aturan #1 — Standalone

> `marshal-threejs` **tidak boleh** berintegrasi dengan `marshal-ai` (Python) maupun
> `marshal-fe` (React). Tidak ada impor lintas folder, tidak ada shared package, tidak ada
> pemanggilan API ke kedua project itu. Semua konsep (peta, flow, batch, report) di-recreate
> sebagai kode TypeScript mandiri di project ini.

Diuji otomatis oleh `bun run check:standalone` (T01) dan masuk Definition of Done global.

## Keputusan yang sudah dikunci

| Topik | Keputusan |
|---|---|
| Framework | TanStack Start (default, SSR-capable) + TypeScript strict, Vite |
| Simulasi | Berjalan di browser (client-side); satu tab = satu batch; fixed-step 0.1 s; seeded RNG |
| Renderer | three.js plain (`three`), scene builder framework-agnostic (`src/render/`) |
| Visi untuk Gemini | Capture offscreen dari canvas WebGL yang sama (gate 4 view, track per step) → JPEG base64; tanpa browser headless, tanpa Playwright di runtime |
| AI | Google ADK TypeScript (`@google/adk`) server-side, dipanggil lewat TanStack Start server function; model default `gemini-2.5-flash` |
| Kunci API | Hanya di server (env); tidak pernah masuk bundle client |
| Mode offline | Tanpa kunci Gemini, seluruh batch tetap jalan dengan jawaban AI deterministik (`src/ai/offline.ts`) dan `degraded: true` |
| Persistensi | In-memory + localStorage; export/download JSON; tanpa database |
| Jumlah mobil | 1–6 per batch; 1 batch aktif (batch kedua ditolak) |
| Fault yang digenerate | `wrong_colour` (N≥2), `dead_lamp` (N≥3, `headlights`/`brake_lights`), `incident` box+worker (N≥3); sisanya `clean` |
| Flow 6–7 | Hanya dijalankan jika ada insiden; jika tidak, status `not_triggered` |
| Tes | Vitest + Playwright; DoD harus lulus tanpa kunci Gemini |

## Arsitektur singkat

```text
Browser (satu tab)
├─ src/sim/      mesin simulasi deterministik (pure TS, tanpa three/React)
│   ├─ engine    tick loop, kendaraan kinematik, misi, routine
│   ├─ flows/    flow 1-5 (yard, assistance, visual, functional, report)
│   ├─ lab/      flow 6-7 (scenario generator, adversarial tester)
│   └─ batch/    manifest + label, orkestrasi batch, counts, report
├─ src/render/   scene three.js + capture kamera (gate/track) → JPEG
├─ src/ui/       dashboard (input, progress, 7 flow card, tabel, log, report)
└─ src/stores/   state (zustand), event log, batch state

TanStack Start server (Node)
└─ src/ai/       ADK agents + server function `aiTask()` → Gemini
                 (tanpa kunci: offline deterministik)
```

Alasan client-side: capture frame tidak butuh browser headless, viewer 60 fps langsung dari
state yang sama, dan replay deterministik cukup dari seed. Konsekuensi: batch berhenti bila
tab ditutup — diterima untuk demo (dicatat di T18).

## Urutan pengerjaan

| ID | Task | Depends on |
|---|---|---|
| T01 | [Scaffold project standalone](T01-scaffold.md) | — |
| T02 | [World & data model](T02-world-data-model.md) | T01 |
| T03 | [Sim engine deterministik](T03-sim-engine.md) | T02 |
| T04 | [Flow 1 — Yard traffic](T04-flow1-yard.md) | T03 |
| T05 | [Vision capture three.js](T05-vision-capture.md) | T02, T03 |
| T06 | [Flow 3 — Visual inspection](T06-flow3-visual.md) | T04, T05 |
| T07 | [Flow 4 — Functional check](T07-flow4-functional.md) | T05 |
| T08 | [Flow 2 — Remote assistance](T08-flow2-assistance.md) | T04 |
| T09 | [Flow 5 — Report & rework](T09-flow5-report.md) | T06, T07, T08 |
| T10 | [Gemini + ADK layer](T10-gemini-adk.md) | T06, T07, T08 |
| T11 | [Flow 6 — Scenario generator](T11-flow6-scenario.md) | T03, T10 |
| T12 | [Flow 7 — Adversarial tester](T12-flow7-adversarial.md) | T11 |
| T13 | [Batch orchestrator](T13-batch-orchestrator.md) | T09 |
| T14 | [Batch feedback & report](T14-batch-report.md) | T13 |
| T15 | [Dashboard UI](T15-dashboard-ui.md) | T13, T14 |
| T16 | [3D viewer](T16-3d-viewer.md) | T05, T13 |
| T17 | [Tests & acceptance](T17-tests-acceptance.md) | semua |
| T18 | [Docs & runbook](T18-docs-runbook.md) | T17 |

Fase: **1** fondasi (T01–T03) · **2** flow 1–5 + AI (T04–T10) · **3** flow 6–7 (T11–T12)
· **4** batch & UI (T13–T16) · **5** kualitas (T17–T18).

Estimasi total: **±24–30 hari orang**.

## Alur data utama

1. User memilih jumlah mobil (1–6) → `createBatch(n)` (zustand + `src/sim/batch/`).
2. Manifest deterministik digenerate: VIN, build sheet, warna, truck, label yang diinjeksi,
   mobil insiden (seed disimpan untuk replay).
3. `SimEngine` menjalankan batch: line end → inspection gate → test track → report → yard →
   shipped, dengan flow 1–5 aktif. Setiap kejadian menjadi `BatchEvent` (flow, source, title,
   detail, level).
4. Saat butuh judgement, flow memanggil `aiTask` (server function) membawa konteks/frame JPEG;
   server memanggil ADK/Gemini; error/timeout → fallback offline, batch tetap jalan.
5. Jika ada insiden: setelah semua mobil selesai, flow 6 (generator) dan flow 7 (adversarial)
   dijalankan terhadap skenario insiden.
6. `buildReport()` menyusun counts, KPI per flow, label expected vs actual, dan memanggil
   `batch.feedback` untuk summary + 7 feedback flow + risiko.
7. Dashboard berhenti polling saat status terminal dan menampilkan report + tombol download JSON.

## Kontrak status batch

```text
queued → running → done
                 ↘ failed
                 ↘ cancelled
```

`progress = finished_cars / requested_cars * 100`, `finished` = mobil `yard`, `shipped`,
atau selesai `rework`.

## Definition of Done global

- [ ] `bun run lint`, `bun run typecheck`, `bun run test` lulus.
- [ ] `bun run build` lulus.
- [ ] `bun run check:standalone` lulus (tidak ada referensi ke marshal-ai / marshal-fe).
- [ ] `bunx playwright test` lulus **offline** (tanpa kunci Gemini).
- [ ] Smoke: batch 1 mobil dan 3 mobil selesai, report berisi 7 flow.
- [ ] Tanpa kunci Gemini: batch tetap `done`, report dibuat, badge `degraded` tampil.
- [ ] Tidak ada secret/credential yang masuk repo (`.env` di-gitignore, ada `.env.example`).
