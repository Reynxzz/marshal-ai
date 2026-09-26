#!/usr/bin/env python3
"""Run one serial Day 2 phase: four gate views or R-12 lighting steps 1-4."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from pathlib import Path

from marshal_ai.driving import MissionClient, MissionDone, MissionGo, Status, TrackStep
from marshal_ai.driving.carla_backend import CarlaDrivingSystem, GATE_CAMERAS, _offset
from marshal_ai.driving.routines import TELEMETRY_CHECKS
from marshal_ai.events import InMemoryBus
from marshal_ai.fakeworld import GATE_VIEWS
from marshal_ai.plant import load_plant_map

LIGHT_STEPS = [1, 2, 3, 4]
EXPECTED_LIGHT = {
    "headlights": "LowBeam",
    "indicators": "LeftBlinker",
    "brake_lights": "Brake",
    "reverse": "Reverse",
}


class _FixedEvidenceCamera:
    """One fixed camera created before the phase vehicle and reused until cleanup."""

    def __init__(self, driving: CarlaDrivingSystem):
        self.driving = driving
        self.actor = None
        self._latest = None
        self._sequence = 0
        self._condition = threading.Condition()

    def start(self, transform) -> None:
        world = self.driving.world
        blueprint = world.get_blueprint_library().find("sensor.camera.rgb")
        blueprint.set_attribute("image_size_x", "640")
        blueprint.set_attribute("image_size_y", "360")
        self.actor = world.spawn_actor(blueprint, transform)

        def receive(image) -> None:
            with self._condition:
                self._latest = image
                self._sequence += 1
                self._condition.notify_all()

        self.actor.listen(receive)
        self.wait_fresh(0)

    def wait_fresh(self, sequence: int, timeout: float = 10.0):
        with self._condition:
            self._condition.wait_for(lambda: self._sequence > sequence, timeout=timeout)
            if self._sequence <= sequence or self._latest is None:
                raise RuntimeError("evidence camera produced no fresh frame")
            return self._latest

    def capture_at(self, transform, path: Path) -> int:
        if self.actor is None:
            raise RuntimeError("evidence camera is not started")
        if transform is not None:
            self.actor.set_transform(transform)
        time.sleep(0.75)
        return self.save_latest(path)

    def save_latest(self, path: Path) -> int:
        with self._condition:
            image = self._latest
        if image is None:
            raise RuntimeError("evidence camera has no frame")
        image.save_to_disk(str(path))
        return path.stat().st_size

    def stop(self) -> None:
        if self.actor is not None:
            self.actor.stop()
            self.actor.destroy()
            self.actor = None


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", required=True, choices=("gate", "track"))
    ap.add_argument("--view", choices=tuple(GATE_VIEWS),
                    help="gate phase only: capture one fixed view and merge it")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--vin", default="VIN-DAY2")
    ap.add_argument("--output", type=Path, default=Path("/tmp/marshal-day2"))
    ap.add_argument("--timeout", type=float, default=180.0)
    return ap


def _new_driving(args: argparse.Namespace) -> CarlaDrivingSystem:
    plant_map = load_plant_map()
    driving = CarlaDrivingSystem(
        plant_map,
        host=args.host,
        port=args.port,
        fixed_delta_s=0.05,
        expected_version="0.9.16",
    )
    if not driving.world.get_map().name.endswith("Town05"):
        raise RuntimeError(f"Day 2 demo requires Town05, got {driving.world.get_map().name}")
    for actor in driving.world.get_actors().filter("vehicle.*"):
        if actor.attributes.get("role_name") == args.vin:
            actor.destroy()
    return driving


def _gate_transform(driving: CarlaDrivingSystem, view: str):
    # Town05's camera stream fails at the east-side pose on both GCP and AWS.
    # A fixed left-side sensor plus a 180-degree stationary vehicle rotation gives
    # the true opposite body side without using that invalid renderer pose.
    camera_view = "left" if view == "right" else view
    return _offset(driving, "inspection_gate", *GATE_CAMERAS[camera_view])


def _destroy_vehicles(driving: CarlaDrivingSystem) -> None:
    for vehicle in list(driving.vehicles.values()):
        vehicle.actor.destroy()


def _write_combined_manifest(output: Path) -> None:
    gate_path, track_path = output / "gate.json", output / "track.json"
    if not gate_path.exists() or not track_path.exists():
        return
    gate = json.loads(gate_path.read_text())
    track = json.loads(track_path.read_text())
    manifest = {
        "result": "success",
        "map": "Carla/Maps/Town05",
        "vin": track["vin"],
        "gate": gate["gate"],
        "track": track["track"],
        "status_count": track["status_count"],
        "scope": "R-12 lighting steps 1-4; physical reverse/brake/full-lock maneuvers deferred",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))


async def run_gate(args: argparse.Namespace) -> dict:
    import carla  # noqa: F401 - validates the live client before actors are created

    args.output.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict] = {}
    gate_json = args.output / "gate.json"
    if args.view is None:
        for pattern in ("gate_*.jpg", "gate.json", "manifest.json"):
            for old_artifact in args.output.glob(pattern):
                old_artifact.unlink()
    elif gate_json.exists():
        existing = {item["view"]: item for item in json.loads(gate_json.read_text())["gate"]}

    selected_views = [args.view] if args.view else list(GATE_VIEWS)
    driving = _new_driving(args)
    camera = _FixedEvidenceCamera(driving)
    captured: dict[str, dict] = {}
    try:
        first_view = selected_views[0]
        camera.start(_gate_transform(driving, first_view))
        driving.spawn(args.vin, at="inspection_gate", colour="0,80,200")
        if selected_views == ["right"]:
            vehicle = driving.vehicles[args.vin].actor
            transform = driving.transform("inspection_gate")
            transform.rotation.yaw += 180.0
            vehicle.set_transform(transform)
            driving.world.wait_for_tick(seconds=10.0)
        for index, view in enumerate(selected_views):
            path = args.output / f"gate_{view}.jpg"
            transform = None if index == 0 else _gate_transform(driving, view)
            size = camera.capture_at(transform, path)
            item = {"view": view, "image": path.name, "bytes": size}
            captured[view] = item
            print(json.dumps({"type": "gate.capture", **item}), flush=True)
    finally:
        camera.stop()
        _destroy_vehicles(driving)

    merged = {**existing, **captured}
    artifacts = [merged[view] for view in GATE_VIEWS if view in merged]
    if any(item["bytes"] == 0 for item in artifacts):
        raise RuntimeError("gate produced an empty image")
    complete = len(artifacts) == 4
    result = {
        "result": "gate.success" if complete else "gate.partial",
        "vin": args.vin,
        "gate": artifacts,
    }
    gate_json.write_text(json.dumps(result, indent=2, sort_keys=True))
    if complete:
        _write_combined_manifest(args.output)
    print(json.dumps({**result, "output": str(args.output)}, sort_keys=True), flush=True)
    return result


async def run_track(args: argparse.Namespace) -> dict:
    import carla

    args.output.mkdir(parents=True, exist_ok=True)
    driving = _new_driving(args)
    missions = MissionClient(driving, load_plant_map(), InMemoryBus())
    camera = _FixedEvidenceCamera(driving)
    done = asyncio.Event()
    statuses: list[Status] = []
    artifacts: dict[int, dict] = {}
    started = False

    def on_event(event) -> None:
        if isinstance(event, Status):
            statuses.append(event)
        elif isinstance(event, TrackStep):
            path = args.output / f"track_step{event.step}_{event.name}.jpg"
            size = camera.save_latest(path)
            state = driving.vehicles[event.vin].actor.get_light_state()
            bit_name = EXPECTED_LIGHT[event.name]
            bit = getattr(carla.VehicleLightState, bit_name)
            artifacts[event.step] = {
                "name": event.name,
                "image": path.name,
                "bytes": size,
                "telemetry": event.telemetry,
                "telemetry_ok": TELEMETRY_CHECKS[event.name](event.telemetry),
                "expected_light_bit": bit_name,
                "light_bit_active": bool(int(state) & int(bit)),
                "raw_light_state": int(state),
            }
            print(json.dumps({"type": "track.capture", "step": event.step,
                              **artifacts[event.step]}), flush=True)
        elif isinstance(event, MissionDone) and event.mission_id == "M-DAY2-R12-LIGHTS":
            print(event.model_dump_json(), flush=True)
            done.set()

    missions.on_event(on_event)
    try:
        camera.start(_offset(driving, "test_track", -8.0, 3.0, 1.6, -20.0))
        driving.spawn(args.vin, at="inspection_gate", colour="0,80,200")
        await driving.start()
        started = True
        await missions.send(MissionGo(
            mission_id="M-DAY2-R12-LIGHTS",
            vin=args.vin,
            waypoints=["test_track", "report_point"],
            max_speed_kmh=8.0,
            routine="R-12",
            routine_steps=LIGHT_STEPS,
        ))
        await asyncio.wait_for(done.wait(), timeout=args.timeout)
    finally:
        if started:
            await driving.stop()
        camera.stop()
        _destroy_vehicles(driving)

    missing = [step for step in LIGHT_STEPS if step not in artifacts]
    if missing:
        raise RuntimeError(f"routine did not produce track artifacts for steps {missing}")
    if not all(item["bytes"] > 0 and item["telemetry_ok"] and item["light_bit_active"]
               for item in artifacts.values()):
        raise RuntimeError("one or more lighting-step artifacts failed deterministic checks")
    result = {
        "result": "track.success",
        "vin": args.vin,
        "track": [artifacts[step] for step in LIGHT_STEPS],
        "status_count": len(statuses),
    }
    (args.output / "track.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    _write_combined_manifest(args.output)
    print(json.dumps({**result, "output": str(args.output)}, sort_keys=True), flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    asyncio.run(run_gate(args) if args.phase == "gate" else run_track(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
