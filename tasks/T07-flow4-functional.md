# T07 — Flow 4: Functional Check

**Fase:** 2
**Depends on:** T05
**Estimasi:** 1 hari

## Tujuan

Tes fungsional di `test_track`: routine R-12 dijalankan step demi step; setiap step dinilai
dari telemetri + frame kamera; verdict PASS hanya bila keduanya setuju. `dead_lamp` harus
tertangkap di sini.

## File yang disentuh

- `src/sim/flows/functional.ts` (baru) — `FunctionalChecker`
- `src/sim/routines.ts` (perluas dari T03 bila perlu)
- `src/sim/__tests__/functional.test.ts` (baru)

## Spec

- Routine R-12 (default) step: `headlights_on`, `headlights_off`, `brake_lights`, `reverse`,
  `brake_test`. Tiap step: mobil berhenti/bergerak sesuai step, engine mengirim `TrackStep`
  (telemetri `{lamp?, gear?, speedKmh?}` — elektronik selalu "sehat").
- Saat `TrackStep` diterima:
  1. `captureTrack(vin, step)` (T05).
  2. `track.decide` (AI/offline): dari gambar, "lampu menyala seperti yang diharapkan?" →
     `{works, confidence}`.
  3. Aturan telemetri: step yang membutuhkan nilai telemetri tertentu (mis. `brake_test`
     berhenti ≤ 20 m) dinilai dari telemetri.
  4. Verdict step: PASS bila camera & telemetry setuju; jika satu bilang gagal dan lainnya
     lulus → `disagreement` → REVIEW; bila keduanya gagal → FAIL step (`failedCriteria`).
- Akhir routine: `InspectionResult(kind="functional")` dengan `failedChecks[]`.
- `dead_lamp` (`faultStep=headlights|brake_lights`) mematikan emissive lampu di capture (T05)
  sehingga `track.decide` melihat lampu mati → FAIL step terkait.
- KPI: `pass, fail, disagreements` (T13/T14).

## Acceptance criteria

- [ ] Mobil `clean` lulus semua step R-12.
- [ ] Mobil `dead_lamp(headlights)` gagal tepat di step headlights, step lain lulus.
- [ ] Kasus disagreement (stub: kamera ya, telemetri tidak) menghasilkan REVIEW, bukan FAIL.
- [ ] Event flow 4 memuat step, verdict, confidence.

## Verifikasi

```bash
bun run test
```

## Catatan

- Jangan menambah step fisik baru (belokan, tanjakan); routine fixed dan cukup untuk demo.
- Waktu step (durasi) ikut `timeScale`; jangan pakai timer wall-clock.
