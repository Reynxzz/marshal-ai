import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "demo_carla_mission.py"


def _module():
    spec = importlib.util.spec_from_file_location("demo_carla_mission", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_demo_uses_minimal_calibrated_route():
    module = _module()
    args = module.parser().parse_args([])

    assert module.ROUTE == ["inspection_gate", "test_track", "report_point", "yard.Y1"]
    assert args.fixed_delta_s == 0.05
    assert args.max_speed_kmh == 10.0
    assert args.vin == "VIN-DAY1"
