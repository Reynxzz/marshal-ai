# T01 — Scaffold Project Standalone

**Fase:** 1
**Depends on:** —
**Estimasi:** 0.5–1 hari

## Tujuan

Membuat kerangka `marshal-threejs`: TanStack Start + TypeScript strict, dependensi render/AI/test,
konvensi folder, dan **guard standalone** yang otomatis menolak integrasi ke `marshal-ai`/`marshal-fe`.

## File yang disentuh

- Seluruh hasil scaffold TanStack Start (package.json, vite.config.ts, tsconfig, src/routes/…)
- `src/sim/`, `src/render/`, `src/ai/`, `src/ui/`, `src/stores/`, `src/lib/` (folder kosong + `.gitkeep`)
- `scripts/check-standalone.mjs` (baru)
- `.env.example`, `.gitignore`
- `src/routes/index.tsx` (halaman placeholder)

## Langkah

Scaffold dibuat di folder sementara lalu disalin, supaya `tasks/` dan `README.md` tidak tersentuh:

```bash
cd /tmp && npx @tanstack/cli create marshal-threejs-scaffold --blank -y
# salin isi scaffold ke /home/farissbayu/projects/marshal/marshal-threejs/
# (tanpa menimpa README.md dan tasks/)
```

Dependensi:

```bash
bun add three zod @google/adk @google/genai zustand
bun add -D vitest jsdom @testing-library/react @testing-library/jest-dom \
  @playwright/test @biomejs/biome
bunx playwright install chromium
```

Script `package.json` yang wajib ada:

```json
{
  "dev": "vite dev",
  "build": "vite build",
  "lint": "biome check .",
  "typecheck": "tsc --noEmit",
  "test": "vitest run",
  "test:e2e": "playwright test",
  "check:standalone": "node scripts/check-standalone.mjs",
  "verify": "bun run lint && bun run typecheck && bun run check:standalone && bun run test && bun run build"
}
```

Guard standalone (`scripts/check-standalone.mjs`):

- Pindai semua file di `src/` dan `scripts/` terhadap pola `marshal-ai` dan `marshal-fe`.
- Gagal (exit 1) jika menemukan impor relatif yang keluar folder project (`../../marshal-`).
- Gagal jika menemukan pemanggilan HTTP ke host/port project lain.
- Kecualikan `tasks/` dan `README.md` (dokumen boleh menyebut nama project).

Struktur folder (kosong dulu, diisi task berikutnya):

```text
src/
  sim/      # mesin simulasi murni TS (tanpa three/React)
  render/   # scene three.js + capture
  ai/       # ADK agents + server function + offline
  ui/       # komponen React dashboard
  stores/   # zustand stores
  lib/      # util kecil (format, id, storage)
```

`.env.example`:

```bash
# Opsional. Tanpa ini, seluruh aplikasi jalan dengan AI offline deterministik.
GOOGLE_GENAI_API_KEY=
# atau Vertex:
GOOGLE_GENAI_USE_VERTEXAI=
GOOGLE_CLOUD_PROJECT=
GOOGLE_CLOUD_LOCATION=asia-southeast1
GEMINI_MODEL=gemini-2.5-flash
```

## Acceptance criteria

- [ ] `bun install && bun dev` menampilkan halaman placeholder.
- [ ] `bun run verify` lulus di project yang masih kosong.
- [ ] `check:standalone` **gagal** saat ditambah impor uji ke `../marshal-fe/...` (uji manual, hapus lagi).
- [ ] `.env` masuk `.gitignore`; hanya `.env.example` yang di-commit.
- [ ] Tidak ada dependensi Python/uv/CARLA di manifest.

## Verifikasi

```bash
cd marshal-threejs
bun install
bun run verify
bun dev
```

## Catatan

- Jangan pakai `--router-only`: kita butuh server function TanStack Start untuk menyembunyikan kunci Gemini.
- Jangan mengimpor apa pun dari `marshal-fe` (termasuk konfigurasi vite/biome-nya); salin manual bila perlu.
- Node ≥ 20.19 (syarat ADK TypeScript).
