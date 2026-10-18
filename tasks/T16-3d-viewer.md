# T16 — 3D Viewer

**Fase:** 4
**Depends on:** T05, T13
**Estimasi:** 2–3 hari

## Tujuan

Menampilkan simulasi 3D yang sama dengan yang "dilihat" Gemini: plant, mobil (warna + lampu),
aktor insiden, gate, track, yard. Viewer hanya membaca state simulasi — tidak pernah mengubahnya.

## File yang disentuh

- `src/ui/YardScene.tsx` (baru) — komponen React pembungkus renderer
- `src/render/viewer.ts` (baru) — renderer viewer (rAF) memakai `scene.ts` dari T05
- `src/render/interpolate.ts` (baru) — interpolasi pose antar tick
- `src/render/__tests__/interpolate.test.ts` (baru)

## Spec

- **Render loop**: `requestAnimationFrame`; simulasi di-step pada akumulator fixed 0.1 s ×
  `timeScale`; render memakai interpolasi `alpha` antara snapshot terakhir & sebelumnya
  (mobil tidak patah-patah walau tick 10 Hz).
- **Kamera** (pilih via radio):
  - `overview` — top-down seluruh plant;
  - `follow` — mengikuti mobil terpilih (klik mobil di scene atau baris tabel);
  - `gate` / `track` — kamera capture T05 (melihat persis frame yang dikirim ke Gemini).
- **Visual**: mobil = mesh + warna `spawnColour`; lampu menyala/mati sesuai fault; worker &
  box terlihat saat insiden aktif; highlight mobil insiden (ring); label VIN kecil (opsional,
  bisa toggle).
- **Kontrol**: play/pause, speed 1–8×, reset kamera. Semua state tersambung ke store batch
  (T13/T15) — viewer tidak punya state sim sendiri.
- **Performa**: 60 fps dengan ≤ 6 mobil + ≤ 10 aktor; dispose geometry/material saat unmount;
  resize responsive (`ResizeObserver`); devicePixelRatio dibatasi 2.
- WebGL gagal → fallback panel pesan + tombol pindah ke tabel/log (jangan blank).

## Acceptance criteria

- [ ] Batch 3 mobil terlihat bergerak line→gate→track→yard di scene.
- [ ] Mobil `wrong_colour` terlihat merah; `dead_lamp` terlihat lampunya mati.
- [ ] Kamera `gate`/`track` mempertlihatkan frame yang sama dengan hasil `captureGate`/
      `captureTrack` (cek manual berdampingan).
- [ ] Pause menghentikan gerak; speed memengaruhi kecepatan tanpa mengubah hasil akhir batch.
- [ ] Tidak ada memory leak: mount/unmount 5× berturut-turut stabil (cek manual/Playwright).

## Verifikasi

```bash
bun run test
bun dev   # jalankan batch 3 mobil, cek semua kamera & kontrol
```

## Catatan

- Jangan menambah fisik/forklift/mobil lalu lintas dekoratif; setiap objek di scene harus
  merepresentasikan sesuatu yang ada di state (demo jujur, tetap cepat).
- Capture (T05) memakai renderer offscreen terpisah — jangan berbagi canvas dengan viewer.
