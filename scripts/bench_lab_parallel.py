#!/usr/bin/env python3
"""How many lab runs one GPU host can do at once (M3, "8 runs in parallel").

Runs the same batch of seed scenarios from the test library on 1, 2, ... of the given CARLA servers
(scripts/carla_pool.sh) and reports, for each pool size: wall time, runs per minute, speed-up over
one server, run errors and server crashes. Every run is judged, so a pool size that changes the
results (a run that passes alone and fails when the host is busy) shows up too.

    bash scripts/carla_pool.sh up 4
    python3 scripts/bench_lab_parallel.py --ports 2010,2020,2030,2040 --runs 8
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time

from marshal_ai.lab.library import SEEDS
from marshal_ai.lab.runner import CarlaRunner, run_batch, to_test_run
from marshal_ai.lab.spec import ScenarioSpec
from marshal_ai.plant import load_plant_map


def emit(obj) -> None:
    print(json.dumps(obj, sort_keys=True), flush=True)


def restarts(port: int) -> int:
    unit = "carla" if port == 2000 else f"carla-pool@{port}"
    out = subprocess.run(["systemctl", "show", unit, "-p", "NRestarts", "--value"],
                         capture_output=True, text=True).stdout.strip()
    return int(out or 0)


def gpu() -> dict:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
    used, util = (int(x) for x in out.strip().split(", "))
    return {"gpu_mem_mib": used, "gpu_util_pct": util}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ports", required=True, help="servers to use, e.g. 2010,2020,2030")
    ap.add_argument("--sizes", default=None, help="pool sizes to try (default 1..len(ports))")
    ap.add_argument("--runs", type=int, default=8, help="runs per pool size")
    ap.add_argument("--seeds", default="SC-0150,SC-0160", help="seed scenarios to cycle through")
    ap.add_argument("--clips", action="store_true", help="film the runs too, as the lab does")
    ap.add_argument("--run-timeout", type=float, default=600.0)
    args = ap.parse_args()

    ports = [int(p) for p in args.ports.split(",")]
    sizes = [int(s) for s in args.sizes.split(",")] if args.sizes else list(range(1, len(ports) + 1))
    wanted = args.seeds.split(",")
    seeds = [ScenarioSpec.model_validate(e.spec) for e in SEEDS if e.spec["id"] in wanted]
    plant_map = load_plant_map()
    results, base = [], None
    for n in sizes:
        pool = ports[:n]
        runner = CarlaRunner(plant_map, ports=pool, clips=args.clips, timeout_s=args.run_timeout)
        specs = [seeds[i % len(seeds)].model_copy(update={"id": f"{seeds[i % len(seeds)].id}-b{n}r{i}"})
                 for i in range(args.runs)]
        before = {p: restarts(p) for p in pool}
        load = gpu()
        t0 = time.monotonic()
        outcomes = run_batch(runner, specs, parallel=n)
        wall = time.monotonic() - t0
        runs = [to_test_run(s, o) for s, o in zip(specs, outcomes)]
        row = {
            "servers": n, "runs": len(runs), "wall_s": round(wall, 1),
            "runs_per_min": round(60 * len(runs) / wall, 2),
            "errors": sum(1 for o in outcomes if o.error),
            "passed": sum(1 for r in runs if r.passed),
            "crashes": sum(restarts(p) - before[p] for p in pool),
            "sim_s_per_run": round(sum(o.metrics.get("time_to_goal_s", 0) for o in outcomes) / len(runs), 1),
            "gpu_before": load, "gpu_after": gpu(),
            "error_samples": [o.error[-200:] for o in outcomes if o.error][:2],
        }
        base = base or row["runs_per_min"]
        row["speedup"] = round(row["runs_per_min"] / base, 2)
        results.append(row)
        emit({"type": "bench.size", **row})
    emit({"type": "bench.done", "results": [{k: r[k] for k in ("servers", "runs_per_min", "speedup",
                                                              "errors", "passed", "crashes")}
                                            for r in results]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
