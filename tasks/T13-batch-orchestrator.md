# T13 — Batch Orchestrator

**Fase:** 4
**Depends on:** T09
**Estimasi:** 2 hari

## Tujuan

Menjalankan satu batch utuh dari input user: manifest deterministik berlabel, batch lifecycle,
event log, flow counts, progress, cancel, dan persistensi/export. Ini "otak" fitur utama.

## File yang disentuh

- `src/sim/batch/manifest.ts` (baru) — generator manifest + label
- `src/sim/batch/orchestrator.ts` (baru) — `BatchRunner`
- `src/sim/batch/counts.ts` (baru) — flow counts + progress
- `src/stores/batch.ts` (baru) — state batch (zustand)
- `src/lib/storage.ts` (baru) — persist localStorage + export JSON
- `src/sim/__tests__/manifest.test.ts`, `batch.test.ts` (baru)

## Spec

### Manifest (deterministik)

`buildManifest(batchId, cars, seed?)`:

- `cars` 1–6; seed dari input atau diturunkan dari `batchId`; VIN `VIN-<B##>-<n>`.
- Label per aturan:
  - N ≥ 2: tepat satu `wrong_colour` (build sheet biru, spawn merah);
  - N ≥ 3: tepat satu `dead_lamp` (`faultStep` bergantian `headlights`/`brake_lights`), tepat
    satu `incident` (box + worker), sisanya `clean`;
  - N = 1: `clean` (atau satu fault bila diminta eksplisit).
- Warna build sheet: `blue|white|grey|black` → `spawnColour` RGB.
- Truck: minimal 1 (N=1), 2 untuk N ≥ 2; ETA bertingkat `20 + 20*i` menit.
- Output juga dipakai T16 (warna mobil) dan T14 (label expected vs actual).

### `BatchRunner`

- `start({cars, seed?})` → tolak jika ada batch aktif (status `queued|running`).
- Lifecycle: `queued → running → done | failed | cancelled`.
- Menjalankan `SimEngine` + flow 1–5 (T04–T09) dengan `timeScale`; setiap event engine/flow →
  `BatchEvent{batchId, seq, flow, source, title, detail, level, payload}`.
- `finished_cars` = mobil `yard|shipped|selesai rework`; `progress` = `finished/requested*100`.
- Setelah semua mobil selesai: bila ada insiden → jalankan flow 6 (T11) lalu flow 7 (T12);
  simpan di `batch.lab`; jika tidak → `flow6/7.status = "not_triggered"`.
- `cancel()` → stop engine, despawn aktor, status `cancelled`, event terakhir.
- Timeout batch (default 15 menit wall-clock) → `failed` dengan pesan jelas (bukan menggantung).
- Callback `onEvent(cb)` untuk store/UI.

### Flow counts (`counts.ts`)

```ts
{ flow1: {released, waiting, inYard, shipped},
  flow2: {cases, open, resolved, escalated},
  flow3: {pass, review, fail},
  flow4: {pass, fail, disagreements},
  flow5: {pass, rework, shipped},
  flow6: {testRequests, generated, status},
  flow7: {runs, failures, status} }
```

### Persistensi

- State batch besar (events bisa ratusan) tidak ditulis per-tick ke localStorage; tulis
  snapshot tiap 2 detik + saat terminal.
- `exportBatch(batchId): Blob` — JSON lengkap (manifest, counts, report, events tanpa payload
  media base64).
- Riwayat batch terakhir (ringkas) disimpan agar bisa dibuka ulang setelah refresh.

## Acceptance criteria

- [ ] `buildManifest("BAT-01", 3, seed=7)` identik antar pemanggilan; jumlah & label sesuai
      aturan (unit test).
- [ ] Batch 3 mobil (ada insiden) selesai `done`; batch 1 mobil tanpa insiden selesai `done`.
- [ ] Batch kedua saat ada yang aktif ditolak.
- [ ] Cancel menghentikan sim dan tidak menyisakan aktor.
- [ ] Refresh halaman saat batch selesai → report masih bisa dibuka dari riwayat.

## Verifikasi

```bash
bun run test
```

## Catatan

- Semua keacakan lewat RNG T02; jangan pakai `Math.random()` di `src/sim/`.
- Jangan menyimpan frame JPEG di localStorage; simpan hanya deskripsi/ringkasan.
