# T06 — Flow 3: Visual Inspection

**Fase:** 2
**Depends on:** T04, T05
**Estimasi:** 1–1.5 hari

## Tujuan

Gate inspection: mobil berhenti di `inspection_gate`, 4 view di-capture, dinilai Gemini
(multimodal) atau fallback offline, menghasilkan verdict PASS/REVIEW/FAIL + findings, dan
mendeteksi `wrong_colour` terhadap build sheet.

## File yang disentuh

- `src/sim/flows/visual.ts` (baru) — `VisualInspector`
- `src/sim/__tests__/visual.test.ts` (baru)

## Spec

Urutan saat mobil tiba di `inspection_gate` (kecepatan 5 km/h, lalu stop):

1. `captureGate(vin)` (T05) → 4 frame.
2. Tiga panggilan AI bertahap (masing-masing lewat registry T10):
   - `visual.screen` per view — "ada cacat terlihat?" → `{hasDefect, confidence}`;
     view dengan no-defect confidence tinggi dilewati (hemat panggilan berikut).
   - `visual.defects` per view yang lolos screen → `{findings: [{kind, zone, view}]}`.
   - `visual.match` — bandingkan warna/opsi terlihat vs `buildSheet` →
     `{mismatches: [{field, declared, seen}], confidence}`.
3. Verdict:
   - ada mismatch terkonfirmasi (confidence ≥ 0.6) → `FAIL` (kasus `wrong_colour`);
   - ada defect ambigu / confidence rendah (0.6–0.9) → `REVIEW` → `Proposal(kind="review")`
     untuk supervisor;
   - selain itu → `PASS`.
4. Simpan `InspectionResult(kind="visual")`; emit event flow 3
   (`Verdict`, findings, confidence, latency, `degraded` bila offline).
5. Event `review` yang di-clear supervisor dihitung sebagai **false alarm** (KPI T14).
6. PASS → lanjut `test_track` (T07). FAIL → `report_point` langsung (T09).

Offline stub (`truth`): warna body dari snapshot dibanding `buildSheet.colour` → mismatch bila
beda; defect visual (scratch/dent) **tidak digenerate** di MVP (tidak bisa dirender) — jangan
klaim defect yang tidak ada.

## Acceptance criteria

- [ ] `wrong_colour` → FAIL dengan mismatch `colour`, muncul di tabel batch (T13).
- [ ] Mobil `clean` → PASS tanpa panggilan `visual.defects` bila screen sudah yakin bersih.
- [ ] Verdict REVIEW muncul untuk confidence menengah (test dengan stub yang mengembalikan
      0.7) dan membuat proposal.
- [ ] Semua panggilan AI tercatat sebagai event (prompt tidak, ringkasan saja).

## Verifikasi

```bash
bun run test
```

## Catatan

- Jangan menilai dari geometri 3D langsung (mis. bandingkan material) — penilaian harus lewat
  gambar, supaya jalur offline dan online identik dan demo jujur.
- Ambang 0.6/0.9 disimpan sebagai konstanta bernama agar mudah dikalibrasi (T17).
