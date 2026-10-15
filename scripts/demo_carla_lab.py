#!/usr/bin/env python3
"""Day 3 checkpoint: one generated scenario runs in CARLA and is judged pass or fail.

Flow 6 (Scenario Generator) and, optionally, flow 7 (Adversarial Tester) against the
real CARLA server on the GPU VM.

    plain-language request
      -> Gemini (or MARSHAL_LLM=offline) writes a scenario spec
      -> validate against the plant map, fix loop capped at 3 attempts
      -> compile to a ScenarioRunner script (fixed compiler, not the model)
      -> run in CARLA
      -> rule judge decides pass or fail (code, never the model)
      -> engineer review puts it in the test library

Run it on the VM, where CARLA and the `carla` wheel are available:

    cd ~/marshal-app && bash scripts/run_carla_lab.sh
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from marshal_ai.app import MarshalApp
from marshal_ai.config import Settings
from marshal_ai.lab.judge import judge
from marshal_ai.lab.runner import CarlaRunner, to_test_run
from marshal_ai.lab.spec import ScenarioSpec
from marshal_ai.records import Scenario
from marshal_ai.runtime import build_runtime

DEFAULT_REQUEST = (
    "A worker steps out from behind a parked forklift at dusk while a car turns "
    "into the finishing lane."
)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--request", default=DEFAULT_REQUEST,
                    help="plain-language situation to turn into a test")
    ap.add_argument("--source", default="supervisor request")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--output", default="/tmp/marshal-lab",
                   help="directory for the spec, compiled script and judged run")
    ap.add_argument("--run-timeout", type=float, default=300.0,
                    help="seconds allowed for one CARLA scenario run")
    ap.add_argument("--adversarial", type=int, default=0, metavar="BUDGET",
                    help="also run flow 7 with this run budget (0 = skip)")
    ap.add_argument("--batch", type=int, default=1,
                    help="scenarios run concurrently. Keep at 1 for a single CARLA "
                         "server: every compiled run puts the world in synchronous "
                         "mode and ticks it, so concurrent runs corrupt each other. "
                         "The design's 8-way parallelism needs one server per slot.")
    ap.add_argument("--release", default=None,
                    help="then run the whole library against this driving system release "
                         "(demo scene 5); it is blocked if any test fails")
    ap.add_argument("--engineer", default="eng-demo")
    ap.add_argument("--no-clips", action="store_true",
                    help="do not film each run (a clip per test run is the default)")
    return ap


def emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True), flush=True)


def run(args: argparse.Namespace) -> dict:
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    # The lab only needs the map, store and LLM; the CARLA work happens in the
    # compiled scenario subprocess, so the driving system stays out of the way.
    settings = Settings(driving="fake")
    rt = build_runtime(settings)
    if not rt.map.carla_map or not rt.map.carla_map.endswith(("Town05", "Town05_Opt", "OpenDriveMap")):
        raise RuntimeError(f"lab needs a Town05 plant map, got {rt.map.carla_map!r}")

    from marshal_ai.artifacts import make_artifacts
    runner = CarlaRunner(rt.map, host=args.host, port=args.port, timeout_s=args.run_timeout,
                         artifacts=make_artifacts(settings.artifacts, settings.gcp_project,
                                                  settings.bucket),
                         clips=not args.no_clips, clip_dir=out / "clips")
    app = MarshalApp(rt, runner=runner)

    emit({"type": "lab.start", "runner": runner.name, "llm": settings.llm,
          "request": args.request})

    # ---- flow 6: generate, validate, compile, dry run in CARLA -------------
    gen = app.generator.generate(args.request, source=args.source)
    scenario_id = gen.scenario.id
    emit({"type": "lab.generated", "scenario_id": scenario_id, "attempts": gen.attempts,
          "status": gen.scenario.status.value, "errors": gen.errors})

    if not gen.ok:
        (out / "rejected.json").write_text(json.dumps(
            {"scenario_id": scenario_id, "attempts": gen.attempts, "errors": gen.errors,
             "spec": gen.scenario.spec}, indent=2, sort_keys=True))
        raise RuntimeError(f"generation failed after {gen.attempts} attempts: {gen.errors}")

    spec = ScenarioSpec.model_validate(gen.scenario.spec)
    (out / f"{scenario_id}.spec.json").write_text(
        json.dumps(spec.model_dump(mode="json"), indent=2, sort_keys=True))
    if gen.code:
        (out / f"{scenario_id}.scenario.py").write_text(gen.code)

    # ---- judged confirmation run in CARLA ---------------------------------
    outcome = runner.run(spec)
    if outcome.error:
        raise RuntimeError(f"CARLA run failed: {outcome.error}")
    test_run = to_test_run(spec, outcome, scenario_id=scenario_id)
    verdict = judge(spec, outcome.metrics)

    emit({"type": "lab.judged", "scenario_id": scenario_id, "run_id": test_run.run_id,
          "passed": test_run.passed, "failed_criteria": test_run.failed_criteria,
          "metrics": outcome.metrics, "margins": verdict.margins, "clip_uri": test_run.clip_uri,
          "events": outcome.events, "triggered": outcome.triggered})
    (out / f"{scenario_id}.run.json").write_text(
        json.dumps(test_run.model_dump(mode="json"), indent=2, sort_keys=True))

    # ---- engineer review puts an approved spec in the library --------------
    reviewed = app.generator.review(scenario_id, approve=True, engineer=args.engineer,
                                    note="day3 checkpoint")
    emit({"type": "lab.reviewed", "scenario_id": scenario_id,
          "status": reviewed.status.value, "owner": reviewed.owner})

    manifest = {
        "scenario_id": scenario_id,
        "request": args.request,
        "llm": settings.llm,
        "runner": runner.name,
        "attempts": gen.attempts,
        "library_status": reviewed.status.value,
        "pass_criteria": spec.pass_criteria,
        "judged": {
            "run_id": test_run.run_id,
            "passed": test_run.passed,
            "failed_criteria": test_run.failed_criteria,
            "metrics": outcome.metrics,
            "margins": verdict.margins,
        },
        "artifacts": sorted(p.name for p in out.iterdir()),
    }

    # ---- flow 7 (optional): search for a failing variant -------------------
    if args.adversarial > 0:
        app.adversarial.budget = args.adversarial
        app.adversarial.batch = args.batch
        emit({"type": "lab.adversarial.start", "budget": args.adversarial, "batch": args.batch})
        search = app.adversarial.search(scenario_id)
        summary = {
            "runs": len(search.runs),
            "robust": search.robust,
            "report": search.report(),
            "regression_id": search.regression_id,
        }
        if search.minimal is not None:
            summary["minimal"] = {
                "variant": search.minimal.spec.id,
                "changes": search.minimal.changes,
                "failed_criteria": search.minimal.run.failed_criteria,
                "metrics": search.minimal.run.metrics,
            }
        emit({"type": "lab.adversarial.done", **summary})
        # "runs" in the summary is a count, so the per-run detail uses its own key.
        (out / f"{scenario_id}.search.json").write_text(
            json.dumps({"scenario_id": scenario_id, **summary, "trace": [
                {"variant": r.spec.id, "changes": r.changes, "passed": r.run.passed,
                 "failed_criteria": r.run.failed_criteria, "metrics": r.run.metrics}
                for r in search.runs]}, indent=2, sort_keys=True, default=str))
        manifest["adversarial"] = summary
        manifest["artifacts"] = sorted(p.name for p in out.iterdir())

    # ---- scene 5: the release gate over the whole library -------------------
    if args.release:
        gate = app.release_gate
        emit({"type": "lab.release.start", "release": args.release,
              "suite": [s.id for s in gate.suite()], "history": getattr(rt.history, "name", "none")})
        report = gate.evaluate(args.release, runner, parallel=args.batch)
        emit({"type": "lab.release.done", "summary": report.summary(), "blocked": report.blocked,
              "failed": report.failed, "previous_release": report.previous_release,
              "newly_failing": report.newly_failing, "newly_passing": report.newly_passing})
        manifest["release"] = {**report.model_dump(mode="json"), "summary": report.summary(),
                               "blocked": report.blocked}

    library = [s.id for s in rt.store.list(Scenario)]
    manifest["library"] = library
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    return manifest


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = run(args)
    except Exception as e:
        emit({"type": "lab.error", "error": f"{type(e).__name__}: {e}"})
        return 1
    emit({"type": "lab.done", "scenario_id": manifest["scenario_id"],
          "passed": manifest["judged"]["passed"], "output": args.output})
    print(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    # Skips interpreter shutdown, where the CARLA client intermittently aborts; see
    # exit_without_teardown.
    from marshal_ai.carla_connection import exit_without_teardown

    exit_without_teardown(main())
