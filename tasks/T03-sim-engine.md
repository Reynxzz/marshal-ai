# T03 — Sim Engine Deterministik

**Fase:** 1
**Depends on:** T02
**Estimasi:** 1.5–2 hari

## Tujuan

Mesin simulasi inti: tick loop fixed-step, kendaraan bergerak kinematik mengikuti rute peta,
misi & routine, event stream, hold/resume, snapshot untuk viewer. Semua murni TypeScript —
tanpa three.js, tanpa React, tanpa I/O.

## File yang disentuh

- `src/sim/engine.ts` (baru) — `SimEngine`
- `src/sim/vehicle.ts` (baru) — state & pergerakan per mobil
- `src/sim/routines.ts` (baru) — routine R-12 (steps + telemetry)
- `src/sim/__tests__/engine.test.ts` (baru)

## Spec

### `SimEngine`

```ts
class SimEngine {
  constructor(world: World, opts: { seed: number; timeScale?: number })

  spawn(vin: string, at?: string): void          // default "line_end"
  despawn(vin: string): void
  send(cmd: MissionGo | MissionHold | MissionResume): void
  stop(vin: string, reason: "assist" | "hold"): void
  resume(vin: string): void

  step(dt: number): void                          // fixed-step; dipanggil driver luar
  on(cb: (ev: SimEvent) => void): () => void      // event stream (MissionDone, TrackStep, Status…)

  snapshot(): CarSnapshot[]                       // {vin,x,y,yaw,speedKmh,lamps,colour,state}
  setFault(vin: string, fault: { step: string }): void   // dari manifest (dead_lamp)
}
```

- **Fixed-step** `dt = 0.1 s`; driver (viewer/T15 atau test) memanggil `step()` dengan
  accumulator. `timeScale` 1–10 agar batch demo selesai cepat.
- **Gerak**: setiap mobil punya `path: string[]` (dari `world.route`), maju sepanjang segmen
  dengan kecepatan misi (default 20 km/h, gate 5 km/h), `yaw` dihitung dari arah segmen,
  interpolasi posisi antar titik.
- **Status event** tiap 1 detik simulasi: `{vin, location, x, y, speedKmh, missionId, progress}`.
- **MissionDone** saat `path` habis; `progress = travelled / total`.
- **Hold/Resume**: mobil berhenti `durationS` atau sampai resume.
- **Routine**: saat tiba `test_track` dan misi membawa `routine`, emit `TrackStep` per step
  (`{vin, step, name, telemetry}`) dengan durasi per step; telemetri "sehat" tetap dikirim
  meski lampu mati (fault hanya terlihat lewat kamera — T07).
- **Fault**: `setFault` menandai lampu mati (`headlights`/`brake_lights`) pada snapshot;
  memengaruhi capture di T05, bukan gerak.
- Anti-tabrakan sederhana: jarak minimum antar mobil pada segmen sama (queue) — cukup agar
  mobil tidak saling tembus saat antre di line end.

### Determinisme

- Semua waktu memakai jam simulasi (bukan `Date.now()`).
- Dua run dengan seed + timeline perintah yang sama menghasilkan urutan event identik
  (dibuktikan unit test yang menjalankan 200 tick dua kali dan membandingkan log).

## Acceptance criteria

- [ ] Satu mobil menyelesaikan `line_end → inspection_gate → test_track → report_point → yard.Y1`.
- [ ] 6 mobil berjalan bersamaan tanpa posisi bertumpuk di antrean.
- [ ] Routine R-12 mengeluarkan step berurutan dengan telemetri.
- [ ] Hold 30 detik simulasi lalu resume bekerja; assist stop menghentikan mobil.
- [ ] Test determinisme lulus (event log identik antar run).
- [ ] Tidak ada impor three/React/`@google/*` di `src/sim/`.

## Verifikasi

```bash
bun run test
```

## Catatan

- Jangan menambahkan fisika realistis (inersia, steer rate); kinematik lurus antar titik cukup
  dan justru lebih deterministik. Upgrade fisika = keputusan terpisah di luar scope.
- Kecepatan default dipilih agar batch 3 mobil selesai < 3 menit wall-clock pada timeScale 4.
