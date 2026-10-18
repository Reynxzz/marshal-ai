# T12 — Flow 7: Adversarial Tester

**Fase:** 3
**Depends on:** T11
**Estimasi:** 1.5–2 hari

## Tujuan

Mencari varian skenario yang membuat sistem gagal (fragile), menyusutkannya ke variant
minimal, dan menyimpan hasilnya sebagai regression suite. Dijalankan setelah flow 6, dengan
budget kecil (default 4 run), hanya bila ada insiden.

## File yang disentuh

- `src/sim/lab/adversarial.ts` (baru) — search loop
- `src/sim/lab/judge.ts` (baru) — rule judge dari metrik (bukan AI)
- `src/sim/lab/regression.ts` (baru) — jalankan suite + verdict release
- `src/sim/__tests__/adversarial.test.ts` (baru)

## Spec

- **Loop** (budget default 4, maksimum 24):
  1. `adversarial.propose` (AI/offline) → batch varian (≤ 4) dengan `changes` terbatas pada
     dimensi spec (`offsetM`, `timeOfDay`, jumlah/posisi aktor, zona ekuivalen). Offline stub
     memakai ladder deterministik (offset naik, malam, worker di jalur).
  2. Jalankan tiap varian di engine terpisah (seed tetap per varian, waktu dipercepat).
  3. **Judge (kode, bukan AI)**: varian PASS bila `noContact` dan `timeToClear` dalam batas
     spec; selain itu FAIL dengan `failedCriteria[]` dari metrik.
  4. Simpan tiap run sebagai `TestRun{scenarioId, variant, metrics, passed, failedCriteria}`.
  5. Bila ditemukan FAIL → **shrinking**: coba versi lebih ringan dari variant (offset lebih
     besar, siang, aktor lebih sedikit) sampai minimal; simpan `minimal`.
  6. Iterasi berikutnya memakai riwayat run (prompt `adversarial.propose`).
- **Laporan**: `adversarial.report` (AI/offline) → `{explanation, likelyCause, suggestedFix}`
  berdasarkan metrik & criteria; ditampilkan di report batch.
- **Regression suite**: `regression.ts` menjalankan seluruh skenario `in_library` (termasuk
  minimal failing variant) dan menghasilkan verdict `robust | fragile`; dipakai tombol
  "Evaluate release" di UI (stretch, tidak wajib untuk batch).
- Flow 7 gagal/timeout → `flow7.status = "skipped"`; batch tetap `done`.

## Acceptance criteria

- [ ] Dari baseline aman, search dengan seed tetap menemukan minimal satu variant FAIL
      (unit test dengan budget 4).
- [ ] Shrinking menghasilkan variant minimal (offset lebih besar / siang) yang tetap FAIL.
- [ ] `flow_counts.flow7 = {runs, failures, status}` terisi benar di batch report.
- [ ] Judge 100% deterministik dari metrik; AI hanya menjelaskan, tidak menentukan lulus/gagal.

## Verifikasi

```bash
bun run test
```

## Catatan

- Jangan menaikkan budget default di kode; buat sebagai konstanta/env agar demo tidak lambat.
- Runner varian harus membuat engine baru (bukan mengganggu batch utama); cleanup aktor wajib.
