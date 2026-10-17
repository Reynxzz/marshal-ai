"""Scenario runners. Batches run in parallel.

    SimRunner     the built-in simulator: instant, no physics (tests, quick checks)
    CarlaRunner   compiled CARLA scripts on the GPU VM

Every runner measures the same metrics, so the rule judge and the release gate do not care
which one ran. `with_driver()` gives the same runner for another driving system release.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import time
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
# A run that could not even reach its server never tested anything, so it is tried again rather than
# judged. With three servers on a 4-vCPU host, 2 of 6 runs timed out in client.get_world() on one of
# them; judged, a search would have counted a busy server as a failure of the driving system.
CONNECT_RETRIES, CONNECT_RETRY_WAIT_S = 2, 10.0


def _never_connected(outcome: "RunOutcome") -> bool:
    err = outcome.error or ""
    return "waiting for the simulator" in err and "client.get_world()" in err


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


class CarlaRunner:
    """Runs the compiled scenario script in a subprocess against the CARLA server (VM only).

    Every run is filmed by a chase camera (the design keeps a clip with every test run). The mp4 is
    stored through `artifacts` under gs://marshal/lab/<variant>-<id>.mp4 and its URI recorded on
    the TestRun; `clip_dir` also keeps a local copy. `clips=False` skips the camera.

    `ports` runs on several CARLA servers (scripts/carla_pool.sh): each run borrows a free server
    and gives it back, so run_batch(..., parallel=len(ports)) keeps every server busy and no two
    runs ever share one. A compiled run puts its world in synchronous mode and ticks it, and two
    on one server corrupt each other, so without `ports` there is one server and parallel must be 1.
    """

    def __init__(self, plant_map: PlantMap, host: str = "localhost", port: int = 2000,
                 timeout_s: float = 300.0, artifacts=None, clips: bool = True,
                 clip_dir: str | Path | None = None, ports: list[int] | None = None):
        self.map = plant_map
        self.host, self.timeout_s = host, timeout_s
        self.ports = list(ports) if ports else [port]
        self.port = self.ports[0]
        self._free: queue.Queue[int] = queue.Queue()
        for p in self.ports:
            self._free.put(p)
        self.name = f"carla:{host}:{','.join(map(str, self.ports))}"
        self.artifacts = artifacts
        self.clips = clips
        self.clip_dir = Path(clip_dir) if clip_dir else None

    def with_driver(self, driver: DriverProfile) -> "CarlaRunner":
        return self           # on CARLA the release is whatever BehaviorAgent build the VM runs

    def compile(self, spec: ScenarioSpec) -> str:
        return compile_spec(spec, self.map)

    @property
    def parallel(self) -> int:
        """How many runs may go at once: one per server."""
        return len(self.ports)

    def run(self, spec: ScenarioSpec) -> RunOutcome:
        outcome = None
        for attempt in range(CONNECT_RETRIES + 1):
            port = self._free.get()              # wait for a server nobody is running on
            try:
                outcome = self._run_on(spec, port)
            finally:
                self._free.put(port)
            if not _never_connected(outcome):
                return outcome
            time.sleep(CONNECT_RETRY_WAIT_S)
        return outcome

    def _run_on(self, spec: ScenarioSpec, port: int) -> RunOutcome:
        code = compile_spec(spec, self.map)
        name = f"{spec.id}-{uuid.uuid4().hex[:6]}"
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / f"{spec.id}.py"
            script.write_text(code)
            clip = Path(tmp) / f"{name}.mp4"
            cmd = [sys.executable, str(script), "--host", self.host, "--port", str(port)]
            if self.clips:
                cmd += ["--clip", str(clip)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s)
            if proc.returncode != 0:
                return RunOutcome(metrics={}, error=f"on port {port}: {proc.stderr[-2000:]}")
            out = json.loads(proc.stdout.strip().splitlines()[-1])
            clip_uri = self._keep_clip(clip, name) if out.get("clip") else None
        return RunOutcome(out["metrics"], out.get("events", []), out.get("triggered", []),
                          clip_uri=clip_uri)

    def _keep_clip(self, clip: Path, name: str) -> str | None:
        """Store a run's mp4 and return the URI to record; None if there is no clip."""
        if not clip.exists() or clip.stat().st_size == 0:
            return None
        data = clip.read_bytes()
        if self.clip_dir is not None:
            self.clip_dir.mkdir(parents=True, exist_ok=True)
            (self.clip_dir / clip.name).write_bytes(data)
        uri = f"gs://marshal/lab/{name}.mp4"
        return self.artifacts.put(uri, data, "video/mp4") if self.artifacts is not None else uri


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


def make_runner(settings, plant_map: PlantMap, driver: DriverProfile = DriverProfile()) -> Runner:
    """The lab backend: MARSHAL_LAB if set, else CARLA on the CARLA backend and sim otherwise."""
    lab = settings.lab or ("carla" if settings.driving == "carla" else "sim")
    if lab == "carla":
        from marshal_ai.artifacts import make_artifacts
        # MARSHAL_LAB_PORTS=2010,2020,...: run on these servers (scripts/carla_pool.sh) in parallel
        ports = [int(p) for p in os.getenv("MARSHAL_LAB_PORTS", "").split(",") if p.strip()]
        return CarlaRunner(plant_map, settings.carla_host, settings.carla_port,
                           artifacts=make_artifacts(settings.artifacts, settings.gcp_project,
                                                    settings.bucket), ports=ports or None)
    if lab == "sim":
        return SimRunner(plant_map, driver)
    raise ValueError(f"unknown lab backend {lab!r}; use sim or carla")
