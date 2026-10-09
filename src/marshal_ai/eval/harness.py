"""Run a dataset's cases N times and score them."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from marshal_ai.eval.dataset import Dataset, MediaCache
from marshal_ai.eval.metrics import METRICS
from marshal_ai.eval.runners import RUNNERS
from marshal_ai.llm import LLM

FLOWS = tuple(RUNNERS)


async def evaluate(dataset: Dataset, base: Path, llm: LLM, flows: tuple[str, ...] = FLOWS,
                   runs: int = 3, cache: MediaCache | None = None,
                   progress: Callable[[str, str, int, dict], None] | None = None) -> dict:
    """Every case of each flow, `runs` times; the answers and the scores, as one report.

    Cases run one after another: the Gemini client serialises calls anyway, and timings taken
    side by side would not be the latency a car sees.
    """
    cache = cache or MediaCache(base)
    report = {"dataset": dataset.name, "runs": runs,
              "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "flows": {}}
    for flow in flows:
        cases = getattr(dataset, flow)
        if not cases:
            continue
        results: dict[str, list[dict]] = {}
        for case in cases:
            for i in range(runs):
                try:
                    outcome = await RUNNERS[flow](llm, case, cache)
                except Exception as e:          # a crashed run is a result too, not a stop
                    outcome = {"error": f"{type(e).__name__}: {e}"[:300]}
                if "error" not in outcome:
                    results.setdefault(case.id, []).append(outcome)
                else:
                    results.setdefault(f"{case.id}#errors", []).append(outcome)
                if progress:
                    progress(flow, case.id, i + 1, outcome)
        scored = {k: v for k, v in results.items() if not k.endswith("#errors")}
        errors = {k[:-len("#errors")]: v for k, v in results.items() if k.endswith("#errors")}
        report["flows"][flow] = {"metrics": METRICS[flow](cases, scored),
                                 "errors": sum(len(v) for v in errors.values()),
                                 "cases": {c.id: {"note": c.note, "outcomes": scored.get(c.id, []),
                                                  "errors": errors.get(c.id, [])}
                                           for c in cases}}
    report["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return report
