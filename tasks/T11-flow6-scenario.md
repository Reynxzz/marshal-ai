# T11 — Flow 6: Scenario Generator

**Fase:** 3
**Depends on:** T03, T10
**Estimasi:** 1.5 hari

## Tujuan

Mengubah teks insiden (dari flow 2) menjadi skenario uji terstruktur yang bisa dijalankan
simulasi: spec → validasi → compile → dry run. Hanya dijalankan bila batch punya insiden.

## File yang disentuh

- `src/sim/lab/generator.ts` (baru)
- `src/sim/lab/spec.ts` (baru) — zod `ScenarioSpec` + validator
- `src/sim/lab/compile.ts` (baru) — spec → actor & setup di engine
- `src/sim/lab/library.ts` (baru) — daftar skenario + status review
- `src/sim/__tests__/generator.test.ts` (baru)

## Spec

### `ScenarioSpec` (zod)

```ts
{
  id: string;
  request: string;            // teks asal
  zone: string;               // nama zona/segmen di world
  actors: [{ kind: "walker" | "box" | "vehicle"; at: string; offsetM: number }];
  timeOfDay: "day" | "night";
  metrics: ("min_gap_to_person_m" | "time_to_clear_s" | "contact")[];
  expect: { clearsWithinS?: number; noContact: boolean };
}
```

- `generateScenario(request)` → `aiTask("scenario.spec")`; tanpa AI → spec default deterministik
  dari insiden (`box_worker` di zona yang sama).
- `validateSpec(spec)` — semua nama titik ada di `world`, offset masuk akal (0–10 m), kind
  dikenal; maksimum 3 percobaan perbaikan (AI diminta memperbaiki error validasi, lalu gagal →
  status `failed`, flow 7 `skipped`, batch tetap `done`).
- `compileSpec(spec)` — menghasilkan `ScenarioSetup` yang bisa dijalankan `SimEngine`
  (spawn mobil ego + aktor, kondisi awal, metrik yang dikumpulkan).
- **Dry run**: jalankan setup di engine terpisah (seed tetap) selama ≤ 120 detik simulasi;
  catat metrik; status `dry_run_ok`/`dry_run_failed`.
- `library.ts` menyimpan skenario hasil dengan status `draft → in_library` setelah engineer
  approve (tombol di UI T15; approval wajib untuk dipakai flow 7 regression).

## Acceptance criteria

- [ ] Insiden batch menghasilkan `lab.generated {scenarioId, attempts, status}`.
- [ ] Spec dengan titik zona tidak dikenal ditolak validator, dicoba diperbaiki, dan
      berakhir `failed` tanpa menggagalkan batch.
- [ ] Dry run mengumpulkan metrik yang dideklarasikan spec.
- [ ] Tanpa insiden: `flow6.status = "not_triggered"`.

## Verifikasi

```bash
bun run test
```

## Catatan

- Generator tidak boleh menulis kode; hanya data spec (batas keamanan yang sama dengan versi
  Python: "text → spec, never code").
- Jangan menambah aktor 3D baru di luar `walker|box|vehicle`.
