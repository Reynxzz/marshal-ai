# T05 — Vision Capture three.js

**Fase:** 2
**Depends on:** T02, T03
**Estimasi:** 1.5–2 hari

## Tujuan

Scene three.js dari peta plant + kamera kamera: gate (4 view), track (per step), dan
bird's-eye sederhana. Hasil capture berupa JPEG data URL yang identik dengan apa yang
dilihat user di viewer — inilah "mata" Gemini tanpa CARLA.

## File yang disentuh

- `src/render/scene.ts` (baru) — build scene dari `World`
- `src/render/cars.ts` (baru) — mesh mobil + lampu (emissive on/off) + warna body
- `src/render/capture.ts` (baru) — `captureGate`, `captureTrack`
- `src/render/__tests__/capture.test.ts` (baru)

## Spec

### Scene

- Lantai + jalan dari `world.segments` (mesh strip), marka sederhana, label zona (sprite teks
  opsional), gate frame, track lamp post, yard slots, loading bay, truk = box.
- Mobil: box body + cabin; materi body memakai `spawnColour`; lampu depan/belakang =
  emissive material yang bisa `on/off` (fault `dead_lamp` → satu sisi mati).
- Aktor insiden: worker = capsule, box = cube kecil (dipasang T08).
- Lighting tetap: directional (sun) sudut 35°, ambient 0.6 — kontras cukup agar warna dan
  lampu terbaca Gemini. Tanpa bayangan berat (bayangan boleh off demi kecepatan).

### Capture (`capture.ts`)

```ts
type CaptureMode = "render" | "truth";

captureGate(vin: string, views?: ViewName[]): Promise<CaptureFrame[]>  // front|rear|left|right
captureTrack(vin: string, step: number): Promise<CaptureFrame>
// CaptureFrame = { view: string; mime: "image/jpeg"; dataUrl: string; width; height }
```

- Render ke offscreen `WebGLRenderer` (canvas tersembunyi, `preserveDrawingBuffer: true`,
  ukuran 640×400) — **bukan** kanvas viewer — supaya capture tidak mengganggu frame viewer.
- Kamera preset: gate = 4 kamera tetap di sekeliling mobil saat berhenti di `inspection_gate`
  (tinggi 1.5 m, jarak 6 m); track = 1 kamera 3/4 depan kiri, tinggi 2 m.
- JPEG quality 0.85; target < 200 KB/frame; capture 4 view < 50 ms.
- `captureMode: "truth"` mengembalikan `truthDescription` (warna body, lampu on/off, pekerja
  hadir) — dipakai test tanpa WebGL dan fallback bila WebGL context gagal.
- **Pause sim saat capture** (capture sinkron terhadap snapshot) agar deterministik.

### Dipakai oleh

- T06 (gate verdict), T07 (track steps), T08 (clip insiden = 1 frame + deskripsi), T16 (viewer
  memakai `scene.ts` yang sama; kamera viewer terpisah dari kamera capture).

## Acceptance criteria

- [ ] Capture gate 4 view menghasilkan JPEG valid (magic bytes `FF D8`) di Vitest jsdom tidak
      mungkin — test memakai mode `truth`; verifikasi JPEG nyata via Playwright (T17).
- [ ] Mobil `wrong_colour` (merah vs build sheet biru) terlihat jelas berbeda menurut manusia.
- [ ] `dead_lamp` membuat satu lampu redup/mati, terbaca di capture track.
- [ ] Tidak ada import dari `src/sim/` ke `src/render/` (arah dependensi satu arah).

## Verifikasi

```bash
bun run test
bun dev   # cek manual: render 1 mobil di gate, bandingkan 4 view
```

## Catatan

- Jangan pakai react-three-fiber; scene harus bisa hidup tanpa React (dan tanpa DOM viewer).
- Kalau WebGL headless di CI bermasalah, e2e T17 memakai chromium Playwright (SwiftShader)
  yang sudah terpasang di T01 — bukan dependensi baru.
