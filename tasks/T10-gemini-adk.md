# T10 — Gemini + ADK Layer

**Fase:** 2
**Depends on:** T06, T07, T08
**Estimasi:** 2 hari

## Tujuan

Satu pintu untuk semua panggilan AI: registry task (zod schema in/out), ADK TypeScript
server-side, routing dari client via server function, mode offline deterministik, timeout
yang tidak pernah memblokir simulasi, dan `degraded` flag.

## File yang disentuh

- `src/ai/tasks.ts` (baru) — registry task + skema zod per task
- `src/ai/server.ts` (baru) — `createServerFn` `aiTask()`
- `src/ai/client.ts` (baru) — wrapper client (queue, timeout, prioritas)
- `src/ai/offline.ts` (baru) — jawaban deterministik per task
- `src/ai/agents/` (baru) — `coordinator.ts` + specialist per flow (ADK `LlmAgent`)
- `src/ai/__tests__/offline.test.ts`, `tasks.test.ts` (baru)

## Spec

### Registry task (minimal)

| Task | Input | Output |
|---|---|---|
| `visual.screen` | 1 frame JPEG | `{hasDefect, confidence}` |
| `visual.defects` | 1 frame JPEG | `{findings[]}` |
| `visual.match` | frame + build sheet | `{mismatches[], confidence}` |
| `track.decide` | frame + step | `{works, confidence}` |
| `assist.assess` | situation + options + frame/desc | `{ranked[], recommended, risk, peopleOrEquipment}` |
| `yard.exception` | exception + yard state | `{slots[], reason}` |
| `report.write` | findings + bay table | `{summary, steps[]}` |
| `scenario.spec` | text request | `ScenarioSpec` (T11) |
| `adversarial.propose` | history + metrics | `{variations[], reasoning}` |
| `adversarial.report` | run + metrics | `{explanation, likelyCause, suggestedFix}` |
| `batch.feedback` | digest | `BatchFeedback` (T14) |

- Semua output divalidasi zod; output tidak valid dianggap gagal → fallback offline.
- Input media dikirim sebagai base64 (`inlineData`), maksimum 4 frame per panggilan.

### Server (`server.ts`)

- `aiTask({task, payload})` — server function TanStack Start; hanya di sini env
  `GOOGLE_GENAI_API_KEY` / Vertex dibaca.
- Implementasi: ADK `LlmAgent` per task (model `process.env.GEMINI_MODEL ?? "gemini-2.5-flash"`),
  prompt sistem per task, `responseSchema`/`outputSchema` zod bila didukung; tanpa tools.
- `coordinator.ts` — `LlmAgent` "Plant Ops Coordinator" dengan 7 specialist sebagai sub-agent;
  dipakai untuk pertanyaan bebas di dashboard (stretch: T15 tombol "Ask").
- Tanpa kunci → langsung `offline.ts` dan `degraded: true` (tanpa error).

### Client (`client.ts`)

- `aiTask` dibungkus: timeout 20 s per panggilan, retry 1× untuk error jaringan, lalu fallback
  offline; hasil disimpan di log event (`source: "gemini.<task>"`, latency, degraded).
- Panggilan **tidak boleh** menghentikan `SimEngine`: flow menunggu promise, tick tetap jalan
  (capture sudah pause). Bila perlu, batch menampilkan spinner di kartu flow.

## Acceptance criteria

- [ ] Tanpa kunci: seluruh batch demo selesai tanpa satu pun error; semua event AI `degraded`.
- [ ] Dengan kunci: `visual.match` mengembalikan mismatch untuk `wrong_colour` pada frame T05.
- [ ] Output AI yang tidak sesuai skema ditolak dan fallback dipakai (unit test dengan stub).
- [ ] Tidak ada file di `src/` selain `src/ai/server.ts` yang membaca env kunci (cek grep di test).
- [ ] `bun run build` tidak memuat string kunci (uji: build lalu grep bundle).

## Verifikasi

```bash
bun run test
GOOGLE_GENAI_API_KEY=... bun dev   # uji manual 1 mobil
bun run build && grep -r "GOOGLE_GENAI_API_KEY" .output/ || true
```

## Catatan

- Jangan pakai ADK devtools/web di runtime aplikasi; itu hanya untuk debugging agent.
- Jangan menyimpan prompt panjang di kode client — semua prompt hidup di `src/ai/`.
