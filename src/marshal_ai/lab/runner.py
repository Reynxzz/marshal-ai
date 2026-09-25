"""Scenario runners: the built-in simulator locally, CARLA on the GPU VM. Batches run in parallel."""

from __future__ import annotations

import json
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

    def run(self, spec: ScenarioSpec) -> RunOutcome:
        try:
            r = simulate(spec, self.map, self.driver)
        except Exception as e:           # a spec the simulator cannot place is a dry-run failure
            return RunOutcome(metrics={}, error=f"{type(e).__name__}: {e}")
        return RunOutcome(r.metrics, r.events, r.triggered,
                          clip_uri=f"gs://marshal/lab/{spec.id}.mp4")


class CarlaRunner:
    """Runs the compiled scenario script in a subprocess against the CARLA server (VM only)."""

    def __init__(self, plant_map: PlantMap, host: str = "localhost", port: int = 2000,
                 timeout_s: float = 300.0):
        self.map = plant_map
        self.host, self.port, self.timeout_s = host, port, timeout_s
        self.name = f"carla:{host}:{port}"

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
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        return list(pool.map(runner.run, specs))
