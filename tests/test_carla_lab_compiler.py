"""The compiled CARLA scenario must keep the properties that only real CARLA exposed.

Each test here maps to a defect found running flow 6 against the server on the GPU host;
they are cheap string/AST checks so they run without CARLA.
"""

from __future__ import annotations

import ast

import pytest

from marshal_ai.lab.compiler import compile_spec
from marshal_ai.lab.spec import ScenarioSpec
from marshal_ai.plant import load_plant_map

SPEC = {
    "id": "SC-0142",
    "source": "supervisor request",
    "map_zone": "finishing_corner",
    "time_of_day": "dusk",
    "weather": "clear",
    "ego": {"route": "line_end->finishing", "speed_kmh": 8},
    "actors": [
        {"type": "forklift", "state": "parked", "position": "finishing_corner.p3"},
        {"type": "worker", "start": "behind:forklift", "walk_speed_ms": 1.3,
         "trigger": {"ego_distance_m": 9}},
    ],
    "pass_criteria": ["no_contact", "min_gap_to_person_m >= 1.5", "speed_near_person_kmh <= 5"],
}


@pytest.fixture(scope="module")
def code() -> str:
    return compile_spec(ScenarioSpec.model_validate(SPEC), load_plant_map())


def test_the_generated_script_is_valid_python(code):
    ast.parse(code)


def test_the_world_ticks_before_the_agent_reads_the_ego_transform(code):
    """Without this tick BehaviorAgent plans from (0,0,0) and drives into a guardrail."""
    spawn = code.index('ego = world.spawn_actor(')
    agent = code.index('agent = BehaviorAgent(ego')
    assert 'world.tick()' in code[spawn:agent], \
        "the ego spawn must be followed by world.tick() before BehaviorAgent is built"


def test_actors_are_tagged_so_a_timed_out_run_can_be_cleaned_up(code):
    """A leaked actor otherwise blocks the same spawn point for every later run."""
    assert 'LAB_ROLE = "marshal-lab"' in code
    assert 'role_name") == LAB_ROLE' in code, "stale lab actors must be found by role"
    assert 'blueprint(BLUEPRINTS[a["type"]])' in code, "actors must spawn tagged"


def test_a_worker_behind_an_occluder_clears_that_occluder(code):
    """Spawning inside the occluder's bounding box fails; placement must step past it."""
    assert 'bounding_box.extent.x' in code
    assert 'half_len + 1.8' in code


def test_spawning_retries_alternative_placements_before_giving_up(code):
    assert 'candidate placements' in code
    assert 'for cand in candidates' in code


def test_the_run_budget_exceeds_the_route_time(code):
    """line_end -> finishing alone needs about 76 simulated seconds."""
    assert 'default=200.0' in code


def test_cleanup_restores_asynchronous_mode(code):
    """Leaving synchronous mode on stalls the viewer and every other client."""
    tail = code[code.index('finally:'):]
    assert 'settings.synchronous_mode = False' in tail
    assert 'actor.destroy()' in tail


def test_the_finishing_route_is_fully_pinned_to_carla_transforms():
    """The lab route cannot run unless every point on it has a transform."""
    plant_map = load_plant_map()
    route = plant_map.route("line_end", "finishing")
    missing = [p for p in route if not plant_map.points[p].carla]
    assert missing == [], f"lab route points without CARLA transforms: {missing}"


def test_a_box_from_a_stuck_car_case_compiles_with_a_blueprint_in_the_lane():
    spec = {**SPEC, "id": "SC-0143", "actors": [
        {"type": "box", "state": "stationary", "position": "finishing_corner.p2"},
        {"type": "worker", "state": "stationary", "position": "finishing_corner.p2"},
    ], "pass_criteria": ["no_contact", "min_gap_to_person_m >= 1.5"]}
    code = compile_spec(ScenarioSpec.model_validate(spec), load_plant_map())
    ast.parse(code)
    assert "'box': 'static.prop.box01'" in code
    assert 'else 0.0 if a["type"] == "box" else ROADSIDE' in code


def test_the_lab_car_brakes_for_props_as_the_plants_does(code):
    """SC-0142 failed no_contact: the lab's bare BehaviorAgent drove into the box the plant's car stops for."""
    from marshal_ai.driving import carla_backend

    assert (f"OBJECT_STOP_M, OBJECT_LATERAL_M, OBJECT_DECEL_MS2 = {carla_backend.OBJECT_STOP_M}, "
            f"{carla_backend.OBJECT_LATERAL_M}, {carla_backend.OBJECT_DECEL_MS2}") in code, \
        "the lab must test the same stopping rule the plant drives with"
    loop = code[code.index("while t < args.timeout:"):code.index("if contact:")]
    brake, step = loop.index("prop_in_path(world, ego)"), loop.index("ego.apply_control(agent.run_step())")
    assert brake < step, "the prop check must come before the agent's own control"
    assert "HELD_FOR_PROP_S" in loop, "a car held by a box that never moves ends the run"


def test_every_run_can_be_filmed(code):
    assert 'ap.add_argument("--clip"' in code
    assert "clip.drain()" in code[code.index("while t < args.timeout:"):]
    assert 'result["clip"] = args.clip' in code


def test_the_runner_stores_each_runs_clip_and_records_its_uri(tmp_path, monkeypatch):
    import json
    import subprocess
    from types import SimpleNamespace

    from marshal_ai.lab import runner as runner_mod

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        clip = cmd[cmd.index("--clip") + 1]
        with open(clip, "wb") as f:
            f.write(b"mp4-bytes")
        out = {"metrics": {"no_contact": 1.0}, "events": [], "triggered": [], "clip": clip}
        return SimpleNamespace(returncode=0, stdout=json.dumps(out) + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    stored = []

    class Store:
        def put(self, uri, data, content_type="image/jpeg"):
            stored.append((uri, data, content_type))
            return uri.replace("gs://marshal/", "gs://bucket/")

    r = runner_mod.CarlaRunner(load_plant_map(), artifacts=Store(), clip_dir=tmp_path)
    outcome = r.run(ScenarioSpec.model_validate(SPEC))

    [(uri, data, kind)] = stored
    assert uri.startswith(f"gs://marshal/lab/{SPEC['id']}-") and uri.endswith(".mp4")
    assert data == b"mp4-bytes" and kind == "video/mp4"
    assert outcome.clip_uri == uri.replace("gs://marshal/", "gs://bucket/")
    assert [p.read_bytes() for p in tmp_path.iterdir()] == [b"mp4-bytes"], "a local copy is kept"
    assert runner_mod.to_test_run(ScenarioSpec.model_validate(SPEC), outcome).clip_uri == outcome.clip_uri

    r = runner_mod.CarlaRunner(load_plant_map(), clips=False)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps({"metrics": {}}), stderr=""))
    assert r.run(ScenarioSpec.model_validate(SPEC)).clip_uri is None
