# T15 — Dashboard UI

**Fase:** 4
**Depends on:** T13, T14
**Estimasi:** 2–3 hari

## Tujuan

Dasbor utama di route `/`: user memasukkan jumlah mobil dan menjalankan batch, melihat
progress + 7 flow card + tabel mobil + log, memutuskan kartu asistensi/proposal, dan membaca
report Gemini saat selesai. Seluruh UI harus berfungsi penuh tanpa kunci Gemini.

## File yang disentuh

- `src/routes/index.tsx` (ganti placeholder)
- `src/ui/BatchForm.tsx`, `BatchProgress.tsx`, `FlowCards.tsx`, `CarsTable.tsx`,
  `EventLog.tsx`, `ReportCard.tsx`, `AssistCard.tsx`, `ProposalCard.tsx` (baru)
- `src/ui/__tests__/*.test.tsx` (baru)

## Spec

- **BatchForm**: input `type="number"` min 1 max 6, label "Cars in this batch", tombol Run
  (disabled saat batch aktif/berjalan) + helper "1–6 cars. Other data is generated and
  labelled."; tombol Cancel dengan dialog konfirmasi.
- **BatchProgress**: progress bar + status badge (`queued|running|done|failed|cancelled`) +
  `requested/created/finished/shipped` + durasi; `aria-live="polite"`.
- **FlowCards**: 7 kartu (Yard, Assistance, Visual, Functional, Report, Scenario, Adversarial)
  dengan angka dari `flowCounts`; `fail/disagreements` tone danger, `review` warning, `pass`
  success; flow 6–7 `not_triggered` → teks netral "Not triggered (no incident)".
- **CarsTable**: kolom VIN, Expected label (clean/wrong colour/dead lamp `faultStep`/incident),
  State, Stage (Flow N), Gate, Report, Slot/Truck; klik baris → panel detail kecil (build
  sheet + events mobil itu).
- **EventLog**: terbaru di atas, maksimal 200 baris; filter All/Exceptions (`warn`|`bad`);
  kolom time, flow badge, source, title, detail; empty state "Waiting for the batch to start…".
- **ReportCard**: header (batch id, status, model, durasi, size), counts, labels
  caught/missed/false alarms, summary Gemini, 7 feedback flow (observation/issues/
  recommendation), top issues, recommendations, risk badge, tombol **Download JSON**, badge
  "Gemini unavailable, generated from metrics" saat `degraded`.
- **AssistCard / ProposalCard**: muncul saat ada `AssistanceCase` open atau `Proposal`
  pending; tombol opsi (wait_for_removal/reroute/manual_override) / (approve/reject) —
  memanggil aksi di store (T08/T09).
- **Engine controls**: play/pause + speed (1×/2×/4×/8×) memengaruhi `timeScale` runner.
- Tidak ada polling/interval yang jalan setelah status terminal; gunakan subscription store.

## Acceptance criteria

- [ ] Input di luar 1–6 ditolak client (tombol disabled + pesan).
- [ ] Card/angka berubah live selama batch tanpa refresh.
- [ ] Kartu asistensi menghasilkan keputusan yang terlihat di log (`assist.decided`).
- [ ] Report tampil saat `done`, termasuk summary + 7 flow + risk + tombol download.
- [ ] Semua komponen lolos test (render + interaksi dasar) dan `lint/typecheck`.
- [ ] Keyboard-accessible (label, fokus, dialog confirm).

## Verifikasi

```bash
bun run test
bun dev   # batch 3 mobil offline end-to-end
```

## Catatan

- Jangan menaruh logika sim di komponen; semua lewat store `src/stores/batch.ts`.
- Bahasa UI: Inggris singkat (konsisten dengan istilah flow), angka format `id-ID` opsional.
