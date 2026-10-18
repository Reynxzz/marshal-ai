# T08 — Flow 2: Remote Assistance

**Fase:** 2
**Depends on:** T04
**Estimasi:** 1–1.5 hari

## Tujuan

Insiden (box + worker) memblokir jalur mobil insiden; mobil berhenti dan meminta bantuan;
AI merangking opsi dengan alasan/risiko; supervisor memutuskan dari dashboard; aturan
keselamatan (wait hanya bila ada orang; eskalasi 60 detik) berlaku.

## File yang disentuh

- `src/sim/flows/assistance.ts` (baru) — `AssistanceDesk`
- `src/sim/incident.ts` (baru) — penempatan box + worker
- `src/sim/__tests__/assistance.test.ts` (baru)

## Spec

- **Penempatan insiden** (hanya bila `manifest.incident.enabled`, T13): saat mobil insiden
  berangkat dari `line_end`, tandai titik blokir 8–12 m setelah persimpangan pertama pada
  rutenya. Aktor: `box` (1×0.8 m) + `worker` (capsule) 2 m di sampingnya.
- Mobil insiden berhenti sebelum box, state `held`, dan event `assist.request`:
  `{caseId, vin, situation, options: ["wait_for_removal", "reroute", "manual_override"],
  clip: frame atau deskripsi (T05)}`.
- `assist.assess` (AI/offline) merangking opsi → `AssistanceCase` dengan
  `recommended, risk, peopleOrEquipment`. Aturan keselamatan di kode (bukan AI):
  - `wait_for_removal` hanya disarankan valid bila ada orang/equipment dekat;
  - opsi `reroute` hanya bila ada segmen alternatif (cek `world`).
- Supervisor memutuskan via `decide(caseId, option, by)` (dipanggil dari UI T15):
  - `wait_for_removal` → worker pergi (delay simulasi), box tetap; mobil menunggu lalu lanjut;
  - `reroute` → rute ulang menuju tujuan;
  - `manual_override` → box disingkirkan seketika lalu lanjut.
- **Eskalasi**: jika 60 detik simulasi tanpa keputusan → event `warn` + kartu naik prioritas.
- Insiden yang tidak selesai sampai timeout batch → `incident_failed`, batch lanjut
  (mobil boleh di-despawn di cleanup), flow 6–7 `not_triggered`.
- `testRequests[]` (dari insiden) disimpan untuk T11 (flow 6).

## Acceptance criteria

- [ ] Batch N≥3 dengan insiden menghasilkan `assist.request`, card (recommended + risk), dan
      `assist.decided` setelah supervisor memilih.
- [ ] `wait_for_removal` tidak muncul sebagai rekomendasi saat tidak ada orang.
- [ ] Spawn box/worker gagal → event `warn` `incident_not_placed`, batch tetap lanjut.
- [ ] Tanpa insiden: flow 2 `not_triggered` di counts.
- [ ] Escalation 60 s teruji (bisa dengan mempercepat waktu simulasi).

## Verifikasi

```bash
bun run test
```

## Catatan

- Jangan menjalankan insiden pada N=1 dan N=2 (manifest T13 yang menentukan).
- Aktor insiden harus dibersihkan di akhir batch (viewer tidak boleh menyisakan worker liar).
