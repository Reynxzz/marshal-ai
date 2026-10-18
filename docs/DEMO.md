# Demo script (5 minutes)

Run steps 1–4 in offline mode: set `VITE_AI_OFFLINE=true` in `.env` (deterministic, no key
needed). Step 5 compares against a Gemini run: unset the flag and set `GOOGLE_GENAI_API_KEY`.

1. **Offline first run.** `bun dev`, open http://localhost:3000, enter `1`, Run batch.
   Watch it move line → inspection gate → test track → report → yard. The Expected column says
   what the car carries: `clean` passes the gate; a fault is caught by the matching check.

2. **3-car batch.** Enter `3`, Run batch. The batch is drawn from a 20-case library (offline a
   seeded sample, with a key a validated Gemini `batch.plan` pick), so the mix varies per run —
   a typical draw includes:
   - a **wrong colour** (declared sheet colour vs off-sheet paint) → FAIL at the gate;
   - a **dead lamp** (headlights, brake lights or both) → fails a step on the track;
   - an **incident** (blockers vary: box, worker, forklift, work zone) → the car stops and an
     Assistance card appears.

3. **Supervisor decision.** On the Assistance card, pick `manual_override` (or `reroute`). The
   lane clears, the log shows `Assist decided`, and the batch continues.

4. **Flows 6–7.** If the draw included an incident, after the cars finish the Scenario generator
   and the Adversarial tester run. The Adversarial card/ counts show runs and failures, and the
   brittle variant is reported. A draw with no incident leaves flows 6–7 `not_triggered` —
   re-run for another draw (each batch gets a new seed and plan).

5. **Report.** Open the report: 7 per-flow feedback entries, expected-vs-actual labels
   (caught/missed/false alarms), a risk badge, and **Download JSON**. In offline mode the
   `degraded` badge is shown. Then (optional) unset `VITE_AI_OFFLINE`, set
   `GOOGLE_GENAI_API_KEY`, rerun step 2, and compare: the same structure but real model
   feedback and latency — and any model failure now stops the batch instead of degrading.

Tips: use the viewer cameras (overview / follow / gate / track) to see exactly what the vision
capture sees; use Pause and the speed buttons (1×–8×) to slow things down.
