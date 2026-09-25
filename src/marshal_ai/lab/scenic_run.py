"""Worker process for the Scenic runner: runs scenario jobs on MetaDrive, one after another.

MetaDrive keeps one world per process, so the runner starts a few of these and hands each a
list of jobs on stdin. Each job prints one result line on stdout:

    MARSHAL_RESULT {"id": ..., "metrics": {...}, "events": [...], "triggered": [...], "clip": ...}
    MARSHAL_RESULT {"id": ..., "error": "..."}

    python -m marshal_ai.lab.scenic_run < jobs.json
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import traceback
from pathlib import Path

PREFIX = "MARSHAL_RESULT "


def run_job(job: dict) -> dict:
    import scenic

    from marshal_ai.lab.scenic_compiler import compile_scenic
    from marshal_ai.lab.sim import TIMEOUT_S
    from marshal_ai.lab.spec import ScenarioSpec
    from marshal_ai.plant import load_plant_map

    spec = ScenarioSpec.model_validate(job["spec"])
    code = compile_scenic(spec, load_plant_map())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, f"{spec.id}.scenic")
        path.write_text(code)
        params = {"profile": json.dumps(job.get("profile") or {}) if job.get("profile") else None,
                  "clip": job.get("clip")}
        scenario = scenic.scenarioFromFile(str(path), params=params, mode2D=True)
        scene, _ = scenario.generate(maxIterations=20, verbosity=0)
        simulator = scenario.getSimulator()
        try:
            result = simulator.simulate(scene, maxSteps=int(TIMEOUT_S / 0.1) + 20, verbosity=0)
        finally:
            simulator.destroy()
    if result is None:
        return {"id": spec.id, "error": "simulation was rejected"}
    return {"id": spec.id, **json.loads(result.records["result"])}


def main() -> None:
    logging.disable(logging.WARNING)
    jobs = json.load(sys.stdin)
    for job in jobs:
        try:
            out = run_job(job)
        except Exception as e:                       # one bad job must not sink the batch
            out = {"id": job["spec"].get("id"), "error": f"{type(e).__name__}: {e}",
                   "trace": traceback.format_exc()[-1500:]}
        print(PREFIX + json.dumps(out), flush=True)


if __name__ == "__main__":
    main()
