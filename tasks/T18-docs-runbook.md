# T18 — Docs & Runbook

**Fase:** 5
**Depends on:** T17
**Estimasi:** 1 hari

## Tujuan

README yang membuat orang baru bisa menjalankan demo dalam < 10 menit — offline maupun
dengan Gemini — plus penjelasan batasan dengan jujur.

## File yang disentuh

- `README.md` (project) — perluas dari versi awal
- `tasks/README.md` — tandai status task yang selesai (opsional, kolom Status)
- `docs/DEMO.md` (baru) — skrip demo 5 menit
- `docs/ARCHITECTURE.md` (baru) — diagram & alur data (boleh dari tasks/README)

## Spec

### README

1. Apa ini + aturan standalone (kutip aturan #1).
2. Quickstart offline:
   ```bash
   bun install
   bun dev
   # buka http://localhost:3000, input 3 mobil, Run
   ```
3. Menyalakan Gemini (opsional): API key atau Vertex, daftar env, apa yang berubah
   (verdict asli vs `degraded`).
4. Cara lulus verifikasi: `bun run verify`.
5. Peta folder singkat (`src/sim`, `src/render`, `src/ai`, `src/ui`, `src/stores`).
6. Batasan: simulasi berjalan di tab (batch berhenti bila tab ditutup), kinematik tanpa fisika
   realistis, fault yang digenerate hanya `wrong_colour`/`dead_lamp`/insiden, bukan produksi.

### docs/DEMO.md (skrip 5 menit)

1. Offline: batch 1 mobil clean → tunjukkan gate PASS + report.
2. Batch 3 mobil: tunjukkan `wrong_colour` FAIL, `dead_lamp` gagal di track, insiden box+worker
   → kartu asistensi → keputusan supervisor.
3. Flow 6–7 dari insiden → adversarial menemukan variant fragile.
4. Buka report: 7 flow feedback, labels caught/missed, risk, download JSON.
5. (Opsional) Nyalakan Gemini, ulangi batch 3 → tunjukkan feedback asli & latency.

### docs/ARCHITECTURE.md

- Diagram: browser sim + render, server function AI, ADK agents.
- Kontrak batch (status, counts) dan event shape.
- Keputusan desain penting (client-side sim, capture dari WebGL, offline-first).

## Acceptance criteria

- [ ] Dari clone bersih tanpa `.env`, mengikuti README menghasilkan batch `done` < 10 menit.
- [ ] Langkah menyalakan Gemini terdokumentasi lengkap (API key & Vertex).
- [ ] Batasan tertulis jujur (tidak mengklaim fisika/realitas yang tidak ada).
- [ ] Semua perintah di README benar-benar bisa disalin-tempel.

## Verifikasi

```bash
# dari clone bersih di direktori sementara
git clone <repo> /tmp/m3-check && cd /tmp/m3-check && bun install && bun run verify
```

## Catatan

- Jangan menduplikasi isi task ke README; README untuk pemakai, `tasks/` untuk pengembang.
- Jangan menulis janji roadmap di README; cukup apa yang ada hari ini.
