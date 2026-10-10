"""python -m marshal_ai.eval DATASET [--flows gate,track,assist] [--runs N] [--model M]"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from marshal_ai.eval.dataset import load_dataset
from marshal_ai.eval.harness import FLOWS, evaluate
from marshal_ai.llm import GeminiLLM


def _commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _summary(report: dict) -> str:
    budget = report.get("thinking_budget")
    lines = [f"dataset {report['dataset']}, model {report.get('model')}"
             + (f" (thinking budget {budget})" if budget is not None else "") + f", {report['runs']} runs per case"]
    for flow, body in report["flows"].items():
        lines.append(f"\n{flow}" + (f"  ({body['errors']} errored runs)" if body["errors"] else ""))
        for name, value in body["metrics"].items():
            if isinstance(value, dict):
                value = ", ".join(f"{k} {v}" for k, v in value.items())
            lines.append(f"  {name:<28} {value}")
        for case_id, case in body["cases"].items():
            answers = [o.get("final_bays", o.get("verdict", o.get("camera_ok", o.get("recommended"))))
                       for o in case["outcomes"]]
            lines.append(f"    {case_id:<34} {answers}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--flows", default=",".join(FLOWS), help=f"comma separated, of {', '.join(FLOWS)}")
    ap.add_argument("--limit", type=int, default=None, help="only the first N cases of each flow")
    ap.add_argument("--runs", type=int, default=3, help="runs per case")
    ap.add_argument("--model", default=None, help="Gemini model (default: MARSHAL_MODEL)")
    ap.add_argument("--thinking-budget", type=int, default=None,
                    help="tokens the model may think for (default: the model's own)")
    ap.add_argument("--out", type=Path, default=Path("eval/results"))
    args = ap.parse_args(argv)
    flows = tuple(f.strip() for f in args.flows.split(",") if f.strip())
    if unknown := set(flows) - set(FLOWS):
        raise SystemExit(f"unknown flows: {sorted(unknown)}")

    from marshal_ai.config import settings
    model = args.model or settings.model
    dataset, base = load_dataset(args.dataset)
    if args.limit:
        for flow in FLOWS:
            setattr(dataset, flow, getattr(dataset, flow)[: args.limit])

    def progress(flow: str, case_id: str, run: int, outcome: dict) -> None:
        answer = outcome.get("error") or outcome.get("final_bays", outcome.get(
            "verdict", outcome.get("camera_ok", outcome.get("recommended"))))
        print(f"  {flow} {case_id} run {run}: {answer} ({outcome.get('seconds', '-')} s)",
              file=sys.stderr, flush=True)

    llm = GeminiLLM(model, thinking_budget=args.thinking_budget)
    report = asyncio.run(evaluate(dataset, base, llm, flows, args.runs,
                                  progress=progress))
    report.update(model=model, thinking_budget=args.thinking_budget, commit=_commit())
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{dataset.name}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(_summary(report))
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
