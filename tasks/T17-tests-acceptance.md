# T17 — Tests & Acceptance

**Fase:** 5
**Depends on:** semua
**Estimasi:** 1.5–2 hari

## Tujuan

Menutup proyek dengan bukti: unit test loreng kritis, e2e offline end-to-end, guard
standalone, dan skrip verifikasi tunggal yang bisa dijalankan dari nol.

## File yang disentuh

- `src/**/__tests__/*` (lengkapi yang belum)
- `e2e/batch.spec.ts`, `e2e/assist.spec.ts`, `e2e/viewer.spec.ts` (baru)
- `playwright.config.ts` (webServer = `bun dev`)
- `scripts/verify.sh` atau script `bun run verify` (sudah ada dari T01)

## Spec

### Unit (Vitest) — minimal

- `world`/`rng`: routing terpendek + determinisme.
- `engine`: determinisme 200 tick; hold/resume; 6 mobil tanpa tumpang tindih.
- `manifest`: N=1,2,3,6 → label & jumlah tepat; repeatable dengan seed.
- `visual`/`functional`: wrong_colour FAIL; dead_lamp gagal di step yang benar; disagreement →
  REVIEW (semua mode offline).
- `assistance`: rekomendasi wait hanya bila ada orang; escalation 60 s.
- `report`/`feedback`: perFlow 7, batchSize == createdCars, fallback saat AI gagal.
- `offline`: setiap task di registry punya handler offline (test iterasi registry).
- `standalone`: `check-standalone.mjs` lulus lewat `bun run check:standalone`.

### E2E (Playwright, offline — tanpa kunci Gemini)

1. **batch.spec**: buka `/`, input 3, Run → tunggu status `done` (timeout 240 s) → assert:
   ReportCard tampil, 7 flow card terisi, tabel berisi 3 VIN dengan 1 FAIL (`wrong_colour`)
   dan 1 dead_lamp (Report/Functional), tombol Download JSON menghasilkan file.
2. **assist.spec**: batch 3 (pasti ada insiden) → AssistCard muncul → pilih opsi →
   log memuat `assist.decided` → batch lanjut.
3. **viewer.spec**: canvas WebGL ada & tidak blank (screenshot non-uniform), play/pause
   bekerja, kamera gate/track bisa dipilih.

### `bun run verify` (dari T01, dipakai sebagai gate)

```bash
bun run lint
bun run typecheck
bun run check:standalone
bun run test
bun run build
bun run test:e2e
```

## Acceptance criteria

- [ ] `bun run verify` lulus dari clone bersih (tanpa `.env`).
- [ ] E2E batch 1 dan 3 mobil lulus; report berisi 7 flow; degraded badge tampil.
- [ ] Guard standalone gagal bila ditambah impor lintas project (uji negatif terdokumentasi).
- [ ] Tidak ada flaky: e2e deterministik karena seed & waktu simulasi (jalankan 3× berturut).
- [ ] Tidak ada test yang butuh kunci Gemini atau jaringan internet.

## Verifikasi

```bash
bun run verify
bunx playwright test --repeat-each=3
```

## Catatan

- Jangan menambah visual snapshot test untuk scene 3D (rapuh); cukup assert canvas tidak
  blank + state tekstual.
- Simpan screenshot e2e hanya saat gagal (`screenshot: "only-on-failure"`).
