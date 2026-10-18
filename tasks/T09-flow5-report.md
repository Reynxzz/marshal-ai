# T09 — Flow 5: Report & Rework

**Fase:** 2
**Depends on:** T06, T07, T08
**Estimasi:** 1–1.5 hari

## Tujuan

Menggabungkan verdict visual + functional menjadi `CarReport`, mengelola alur rework
(bay berurutan → reinspection), dan memastikan hanya mobil PASS yang masuk yard/shipped.

## File yang disentuh

- `src/sim/flows/report.ts` (baru) — `ReportRouter`
- `src/sim/rework.ts` (baru) — antrean bay
- `src/sim/__tests__/report.test.ts` (baru)

## Spec

- **Merge verdict**: `FAIL` menang; lalu `REVIEW`; lalu `PASS`. Bila ada REVIEW yang belum
  diputus supervisor → mobil menunggu di `report_point`.
- **PASS** → `CarReport(verdict=PASS)`; assign slot yard (T04) → `yard` → `shipped` saat truck
  memuat.
- **Non-PASS** →
  1. `report.write` (AI/offline) menyusun summary + langkah rework dari findings;
  2. lengkapi rencana terhadap tabel bay (`rework.paint → body → electrical → mechanical`);
  3. buat `Proposal(kind="rework")` → supervisor confirm/tolak.
- **Rework loop**: `reworkDone(vin)` menutup ticket satu bay lalu mobil pindah ke bay
  berikutnya; setelah bay terakhir → reinspection (visual gate atau functional track sesuai
  temuan) → verdict baru. Maksimum 2 siklus; bila masih FAIL → state `held` + event `bad`.
- Mobil `rework` baru dihitung `finished` setelah tiba di bay (bukan saat dispatch).
- KPI: `pass, rework, shipped, rework_rate` (T14).

## Acceptance criteria

- [ ] Mobil `dead_lamp` menyelesaikan alur rework penuh dan akhirnya PASS + shipped.
- [ ] Mobil `wrong_colour` FAIL → rework → setelah reinspection tetap FAIL bila warna tidak
      berubah; mood test memakai stub yang memperbaiki warna.
- [ ] Proposal rework tanpa approve menahan mobil (tidak mengunci batch selamanya — timeout
      jadi `held` + event).
- [ ] Report verdict konsisten dengan inspection results (unit test).

## Verifikasi

```bash
bun run test
```

## Catatan

- Jangan menyimpan aturan bay di komponen UI; tabel bay hidup di `src/sim/`.
- Summary rework dari Gemini tidak boleh menambah temuan yang tidak ada di records — validasi
  zod dan buang item yang tidak merefer findings.
