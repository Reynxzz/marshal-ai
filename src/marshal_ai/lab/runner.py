"""Scenario runners. Batches run in parallel.

    SimRunner     the built-in simulator: instant, no physics (tests, quick checks)
    ScenicRunner  Scenic programs on MetaDrive: real physics and line of sight, on a laptop
    CarlaRunner   compiled CARLA scripts on the GPU VM

Every runner measures the same metrics, so the rule judge and the release gate do not care
which one ran. `with_driver()` gives the same runner for another driving system release.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from marshal_ai.lab.compiler import compile_spec
from marshal_ai.lab.judge import judge
from marshal_ai.lab.sim import DriverProfile, simulate
from marshal_ai.lab.spec import ScenarioSpec
from marshal_ai.plant import PlantMap
from marshal_ai.records import TestRun

PARALLEL = 8
CLIPS_DIR = Path("runs/clips")


@dataclass
class RunOutcome:
    metrics: dict[str, float]
    events: list[str] = field(default_factory=list)
    triggered: list[int] = field(default_factory=list)
    clip_uri: str | None = None
    error: str | None = None


class Runner(Protocol):
    name: str

    def run(self, spec: ScenarioSpec) -> RunOutcome: ...


class SimRunner:
    def __init__(self, plant_map: PlantMap, driver: DriverProfile = DriverProfile()):
        self.map = plant_map
        self.driver = driver
        self.name = f"sim:{driver.name}"

    def with_driver(self, driver: DriverProfile) -> "SimRunner":
        return SimRunner(self.map, driver)

    def compile(self, spec: ScenarioSpec) -> str:
        return compile_spec(spec, self.map)

    def run(self, spec: ScenarioSpec) -> RunOutcome:
        try:
            r = simulate(spec, self.map, self.driver)
        except Exception as e:           # a spec the simulator cannot place is a dry-run failure
            return RunOutcome(metrics={}, error=f"{type(e).__name__}: {e}")
        return RunOutcome(r.metrics, r.events, r.triggered,
                          clip_uri=f"gs://marshal/lab/{spec.id}.mp4")


class ScenicRunner:
    """Runs specs as Scenic programs on MetaDrive (headless) in worker processes.

    MetaDrive keeps one world per process, so a batch is split across `workers` processes,
    each running its share one after another. `clip()` re-runs one spec recording a top-down GIF.
    """

    def __init__(self, plant_map: PlantMap, driver: DriverProfile = DriverProfile(),
                 workers: int | None = None, clips_dir: Path = CLIPS_DIR, timeout_s: float = 900.0):
        self.map = plant_map
        self.driver = driver
        self.workers = workers or max(1, min(4, (os.cpu_count() or 2) // 2))
        self.clips_dir = Path(clips_dir)
        self.timeout_s = timeout_s
        self.name = f"scenic:{driver.name}"

    def with_driver(self, driver: DriverProfile) -> "ScenicRunner":
        return ScenicRunner(self.map, driver, self.workers, self.clips_dir, self.timeout_s)

    def compile(self, spec: ScenarioSpec) -> str:
        from marshal_ai.lab.scenic_compiler import compile_scenic
        return compile_scenic(spec, self.map)

    def run(self, spec: ScenarioSpec) -> RunOutcome:
        return self.run_many([spec])[0]

    def clip(self, spec: ScenarioSpec) -> str | None:
        path = (self.clips_dir / f"{spec.id}-{self.driver.name}.gif").resolve()
        [outcome] = self._run_jobs([self._job(spec, str(path))])
        return outcome.clip_uri if not outcome.error else None

    def run_many(self, specs: list[ScenarioSpec]) -> list[RunOutcome]:
        return self._run_jobs([self._job(s) for s in specs])

    def _job(self, spec: ScenarioSpec, clip: str | None = None) -> dict:
        return {"spec": spec.model_dump(mode="json"), "profile": dataclasses.asdict(self.driver), "clip": clip}

    def _run_jobs(self, jobs: list[dict]) -> list[RunOutcome]:
        if not jobs:
            return []
        n = min(self.workers, len(jobs))
        shares = [list(range(i, len(jobs), n)) for i in range(n)]

        env = dict(os.environ)
        if env.get("PYTHONUTF8") not in (None, "0", "1"):
            env["PYTHONUTF8"] = "1"      # MetaDrive's 3D asset loader sets "on", which Python rejects

        def work(share: list[int]) -> tuple[str, str]:
            proc = subprocess.Popen([sys.executable, "-m", "marshal_ai.lab.scenic_run"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            try:
                return proc.communicate(json.dumps([jobs[i] for i in share]), timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                return proc.communicate()

        with ThreadPoolExecutor(max_workers=n) as pool:
            outputs = list(pool.map(work, shares))
        outcomes: list[RunOutcome | None] = [None] * len(jobs)
        for share, (out, err) in zip(shares, outputs):
            results = [json.loads(line.split(" ", 1)[1]) for line in out.splitlines()
                       if line.startswith("MARSHAL_RESULT ")]
            for k, i in enumerate(share):
                if k >= len(results):
                    tail = "\n".join(l for l in err.splitlines() if not l.startswith("objc["))[-1500:]
                    outcomes[i] = RunOutcome(metrics={}, error=f"scenic worker stopped: {tail}")
                    continue
                r = results[k]
                if r.get("error"):
                    outcomes[i] = RunOutcome(metrics={}, error=r["error"])
                else:
                    clip = r.get("clip")
                    outcomes[i] = RunOutcome(r["metrics"], r.get("events", []), r.get("triggered", []),
                                             clip_uri=Path(clip).as_uri() if clip else None)
        return outcomes


class CarlaRunner:
    """Runs the compiled scenario script in a subprocess against the CARLA server (VM only)."""

    def __init__(self, plant_map: PlantMap, host: str = "localhost", port: int = 2000,
                 timeout_s: float = 300.0):
        self.map = plant_map
        self.host, self.port, self.timeout_s = host, port, timeout_s
        self.name = f"carla:{host}:{port}"

    def with_driver(self, driver: DriverProfile) -> "CarlaRunner":
        return self           # on CARLA the release is whatever BehaviorAgent build the VM runs

    def compile(self, spec: ScenarioSpec) -> str:
        return compile_spec(spec, self.map)

    def run(self, spec: ScenarioSpec) -> RunOutcome:
        code = compile_spec(spec, self.map)
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / f"{spec.id}.py"
            script.write_text(code)
            proc = subprocess.run(
                [sys.executable, str(script), "--host", self.host, "--port", str(self.port)],
                capture_output=True, text=True, timeout=self.timeout_s,
            )
        if proc.returncode != 0:
            return RunOutcome(metrics={}, error=proc.stderr[-2000:])
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        return RunOutcome(out["metrics"], out.get("events", []), out.get("triggered", []),
                          clip_uri=out.get("clip_uri"))


def to_test_run(spec: ScenarioSpec, outcome: RunOutcome, scenario_id: str | None = None,
                params: dict | None = None) -> TestRun:
    if outcome.error:
        return TestRun(run_id=uuid.uuid4().hex[:10], scenario_id=scenario_id or spec.id, variant=spec.id,
                       params=params or {}, passed=False, failed_criteria=[f"run error: {outcome.error}"])
    j = judge(spec, outcome.metrics)
    return TestRun(run_id=uuid.uuid4().hex[:10], scenario_id=scenario_id or spec.id, variant=spec.id,
                   params=params or {}, metrics=outcome.metrics, passed=j.passed,
                   failed_criteria=j.failed, clip_uri=outcome.clip_uri)


def run_batch(runner: Runner, specs: list[ScenarioSpec], parallel: int = PARALLEL) -> list[RunOutcome]:
    if hasattr(runner, "run_many"):
        return runner.run_many(specs)
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        return list(pool.map(runner.run, specs))


def make_runner(settings, plant_map: PlantMap, driver: DriverProfile = DriverProfile()) -> Runner:
    """The lab backend: MARSHAL_LAB if set, else CARLA on the CARLA backend, Scenic on MetaDrive, sim otherwise."""
    lab = settings.lab or {"carla": "carla", "metadrive": "scenic"}.get(settings.driving, "sim")
    if lab == "carla":
        return CarlaRunner(plant_map, settings.carla_host, settings.carla_port)
    if lab == "scenic":
        return ScenicRunner(plant_map, driver)
    if lab == "sim":
        return SimRunner(plant_map, driver)
    raise ValueError(f"unknown lab backend {lab!r}; use sim, scenic or carla")
