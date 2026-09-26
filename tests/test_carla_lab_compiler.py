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
