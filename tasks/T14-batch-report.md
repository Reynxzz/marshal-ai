# T14 — Batch Feedback & Report

**Fase:** 4
**Depends on:** T13
**Estimasi:** 1.5 hari

## Tujuan

Mengubah log satu batch menjadi report yang bisa dibaca: ringkasan operasional, feedback
Gemini per flow 1–7, top issues, rekomendasi, hasil labeling (expected vs actual), dan KPI.

## File yang disentuh

- `src/sim/batch/feedback.ts` (baru) — digest + skema `BatchFeedback` + fallback
- `src/sim/batch/report.ts` (baru) — `buildReport()`
- `src/sim/__tests__/feedback.test.ts`, `report.test.ts` (baru)

## Spec

### Digest

- Batasi ±200 baris dan prioritaskan: (1) semua event `warn`/`bad`, (2) semua keputusan flow
  1–5, (3) hasil flow 6–7, (4) 50 event terakhir.
- Jangan kirim frame/media ke `batch.feedback`; teks + JSON angka saja.

### `BatchFeedback` (zod)

```ts
{
  batchSize: number,                 // divalidasi == createdCars
  summary: string,
  perFlow: [{ flow: 1..7, name, observation, issues: string[], recommendation: string }],
  topIssues: string[],
  recommendations: string[],
  risk: "low" | "medium" | "high"
}
```

- Panggilan `aiTask("batch.feedback")`; offline → `offlineFeedback(digest)` deterministik.
- Validasi pasca-respons: `batchSize == createdCars`; `perFlow` dilengkapi sampai 7 (yang
  hilang `observation: ""`); `risk` dinormalisasi.
- Gagal/timeout → fallback yang sama + `gemini.error` singkat; batch tetap `done`.

### `buildReport()`

```ts
{
  batchId, status, generatedAt, model, batchSize,
  counts: {requested, created, finished, shipped, rework, exceptions},
  durationS,
  flowKpis: { flow1..flow7 },        // dihitung di TS, AI hanya menjelaskan
  labels: { total, caught, missed, falseAlarms, cars: [{vin, expected, caught}] },
  gemini: BatchFeedback & { degraded, error? },
  log: { events, exceptions }
}
```

- `caught`: label `wrong_colour|dead_lamp|incident` yang terdeteksi (FAIL/gagal step/kasus
  ditangani); `missed` sisanya; `falseAlarms` = REVIEW yang di-clear tanpa menemukan cacat.
- KPI flow 6–7 dari `batch.lab`.
- Report disimpan di `batch.report` dan bisa diunduh JSON (`exportReport()`).

## Acceptance criteria

- [ ] `batchSize` pada report selalu sama dengan `createdCars`.
- [ ] `perFlow` tepat 7 entri untuk kasus ada insiden maupun tidak.
- [ ] Tanpa kunci Gemini: report tetap dibuat, `degraded: true`, `gemini.error` kosong.
- [ ] KPI dihitung di TS; unit test memverifikasi angka dari fixture event tetap.
- [ ] Download JSON menghasilkan file valid dan tidak memuat base64 media.

## Verifikasi

```bash
bun run test
```

## Catatan

- Jangan meminta AI menghitung KPI; ia hanya menjelaskan angka yang sudah dihitung.
- Prompt sistem harus melarang mengarang VIN/cacat yang tidak ada di digest.
