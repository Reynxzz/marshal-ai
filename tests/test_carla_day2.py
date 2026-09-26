import importlib.util
from pathlib import Path

from marshal_ai.driving.carla_backend import (EVIDENCE_HEIGHT, EVIDENCE_WIDTH,
                                              CarlaGateCamera)


DAY2_SCRIPT = Path(__file__).parents[1] / "scripts" / "demo_carla_day2.py"


def _day2_module():
    spec = importlib.util.spec_from_file_location("demo_carla_day2", DAY2_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gate_camera_captures_requested_views_sequentially(monkeypatch):
    calls = []

    class Driving:
        def capture(self, transform, width=None, height=None):
            calls.append((transform, width, height))
            return f"image-{len(calls)}".encode()

    monkeypatch.setattr("marshal_ai.driving.carla_backend._offset", lambda ds, point, *offset: offset)
    media = CarlaGateCamera(Driving()).capture("VIN-1", ["front", "left", "right"])

    assert len(calls) == 3
    assert [item.label for item in media] == [
        "front camera", "left camera", "right camera",
    ]
    # Evidence frames must be larger than the viewer's: Gemini could not tell a lit lamp
    # from a dark one at 640x360.
    assert {(w, h) for _t, w, h in calls} == {(EVIDENCE_WIDTH, EVIDENCE_HEIGHT)}
    assert [item.data for item in media] == [b"image-1", b"image-2", b"image-3"]


def test_day2_runner_uses_only_real_lighting_scope():
    module = _day2_module()
    args = module.parser().parse_args(["--phase", "gate"])
    assert args.phase == "gate"
    assert module.LIGHT_STEPS == [1, 2, 3, 4]
    assert module.EXPECTED_LIGHT == {
        "headlights": "LowBeam",
        "indicators": "LeftBlinker",
        "brake_lights": "Brake",
        "reverse": "Reverse",
    }
    assert args.output == Path("/tmp/marshal-day2")


def test_functional_checker_caches_frame_at_track_step_time():
    from types import SimpleNamespace

    from marshal_ai.driving import TrackStep
    from marshal_ai.driving.routines import R12
    from marshal_ai.flows.functional import FunctionalChecker, _Run
    from marshal_ai.llm import Media

    calls = []

    class Camera:
        def clip(self, vin, step, name):
            calls.append((vin, step, name))
            return Media(mime="image/jpeg", data=b"active-light-frame")

    checker = FunctionalChecker(SimpleNamespace(rt=SimpleNamespace(track_camera=Camera())))
    checker.runs["M-1"] = _Run("VIN-1", [R12[0]])
    event = TrackStep(
        vin="VIN-1", mission_id="M-1", routine="R-12", step=1,
        name="headlights", telemetry={"headlights": "ON"},
    )

    checker.on_step(event)

    assert calls == [("VIN-1", 1, "headlights")]
    assert checker.runs["M-1"].clips[1].data == b"active-light-frame"
