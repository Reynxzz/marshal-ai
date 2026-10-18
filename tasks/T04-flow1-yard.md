# T04 — Flow 1: Yard Traffic

**Fase:** 2
**Depends on:** T03
**Estimasi:** 1.5 hari

## Tujuan

Aturan release, antrean, alokasi slot yard per truck, pengecualian (late truck & zona
tertutup) dengan eskalasi ke Gemini + keputusan supervisor, dan shipping guard. Ini flow
pertama yang membuat batch bergerak.

## File yang disentuh

- `src/sim/flows/yard.ts` (baru) — `YardFlow`
- `src/sim/trucks.ts` (baru) — TruckState + ETA
- `src/sim/__tests__/yard.test.ts` (baru)

## Spec

- `YardFlow.tick(engine, dt)` memanggil `releaseNext()`:
  - Mobil `waiting_dispatch` di `line_end` dirilis ke `inspection_gate` bila:
    - mobil sedang di gate ≤ 1 (spacing), dan
    - zona tujuan tidak tertutup.
  - Jarak antre di `line_end`: 9 m antar mobil (atur posisi antrean saat spawn).
- `trucks`: `{truckId, etaMin, cars: vin[]}`. Saat mobil PASS (dari T09), alokasikan slot
  `yard.Yn` terdekat dengan `loading_bay` yang belum terpakai; assign ke truck round-robin.
- **Late truck**: bila ETA diundur > 15 menit simulasi **dan sudah ada mobil parkir**:
  - emit event `warn` + buat `Proposal(kind="replan")` berisi usulan slot baru;
  - alasan/usulan boleh datang dari `aiTask("yard.exception")` (T10); tanpa AI → heuristik
    deterministik;
  - mobil hanya pindah slot setelah supervisor klik Approve di UI (T15).
- **Closed zone**: `closeZone(name)` — rute yang melewati zona itu dihindari; bila rute
  alternatif ada, mobil memakai alternatif (catat event `route.choice`); bila tidak ada,
  mobil ditahan di `line_end` + event `warn`.
- **Shipping guard**: `ship(truckId)` hanya memuat mobil `yard` + report PASS; mobil non-PASS
  ditolak (event `bad`), status mobil → `shipped`.
- KPI yang dihitung flow: `released, waiting, inYard, shipped` (dipakai T13/T14).

## Acceptance criteria

- [ ] 3 mobil dirilis berurutan dengan jarak aman; tidak ada dua mobil di gate bersamaan.
- [ ] Late truck memunculkan proposal replan; sebelum approve, slot tidak berubah.
- [ ] Zone tertutup memaksa rute alternatif dan tercatat sebagai event.
- [ ] Mobil FAIL tidak bisa ikut shipped (unit test).
- [ ] Semua keputusan heuristik jalan tanpa kunci Gemini.

## Verifikasi

```bash
bun run test
```

## Catatan

- Jangan menaruh aturan visual/inspeksi di flow ini; flow 1 hanya lalu lintas & yard.
- Keputusan manusia (approve replan) dipakai juga oleh UI T15 — simpan `Proposal` di store
  batch, bukan state lokal komponen.
