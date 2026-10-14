"""Flow 2's answer: after assist.choose the car does what was chosen.

The decision logic, with fakes, so no CARLA is needed: waiting holds the car until the lane is
clear, a pass or a reroute hands BehaviorAgent a plan round the object and measures it, an option
that cannot be driven leaves the car waiting, and the stand-in brakes for props in its path. The
plans themselves are built from CARLA's waypoints and are checked on the server
(scripts/demo_carla_day5.py --choose).
"""

from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace

import pytest

from marshal_ai.driving import carla_backend
from marshal_ai.driving.carla_backend import CarlaDrivingSystem, _Vehicle
from marshal_ai.driving.messages import AssistChoose


class _Vec(SimpleNamespace):
    def __sub__(self, other):
        return _Vec(x=self.x - other.x, y=self.y - other.y, z=0.0)


@pytest.fixture(autouse=True)
def _stub_carla(monkeypatch):
    if carla_backend.carla is None:
        monkeypatch.setattr(carla_backend, "carla",
                            SimpleNamespace(VehicleControl=lambda **kw: kw))


class _Agent:
    def __init__(self):
        self._behavior = SimpleNamespace(max_speed=8.0)
        self.plans = []

    def set_global_plan(self, plan, stop_waypoint_creation=True, clean_queue=True):
        self.plans.append(plan)


class _Car:
    """A car heading +x, at x along the lane."""

    def __init__(self, x=0.0, y=0.0, kmh=0.0):
        self.id, self.x, self.y, self.kmh = 1, x, y, kmh

    def get_location(self):
        return _Vec(x=self.x, y=self.y, z=0.0)

    def get_transform(self):
        return SimpleNamespace(location=self.get_location(),
                               get_forward_vector=lambda: _Vec(x=1.0, y=0.0, z=0.0))

    def get_velocity(self):
        return _Vec(x=self.kmh / 3.6, y=0.0, z=0.0)

    def apply_control(self, control):
        pass


def _actor(actor_id, type_id, x, y):
    return SimpleNamespace(id=actor_id, type_id=type_id, attributes={},
                           get_location=lambda: _Vec(x=x, y=y, z=0.0))


def _backend(world_actors=()):
    ds = CarlaDrivingSystem.__new__(CarlaDrivingSystem)
    ds.assist_options = ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"]
    ds.assist_outcomes = {}
    ds.auto_assist = True
    ds._simulated_s = 100.0
    ds.vehicles = {}
    ds.world = SimpleNamespace(get_actors=lambda: list(world_actors))
    car = _Car()
    v = ds.vehicles["VIN-1"] = _Vehicle(vin="VIN-1", actor=car, agent=_Agent(), location="test_track")
    v.mission = SimpleNamespace(mission_id="M-1", max_speed_kmh=8.0)
    v.remaining = ["report_point"]
    v.assist_case = "AC-1"
    return ds, v, car


BOX = _actor(7, "static.prop.box01", 9.0, 0.0)


def test_wait_holds_the_car_until_the_object_is_gone():
    ds, v, _car = _backend()
    blocking = [BOX]
    ds._obstacle_ahead = lambda v: (blocking[0], 9.0) if blocking else (None, 14.0)
    v.assist_obstacle = (BOX.id, BOX.get_location())

    asyncio.run(ds.send(AssistChoose(case_id="AC-1", vin="VIN-1", option="wait_for_removal",
                                     chosen_by="supervisor-ana")))
    assert v.assist_case is None and v.waiting_case == "AC-1"
    assert ds._lane_cleared(v) is False, "the box is still there"
    assert asyncio.run(ds._check_blocked(v)) is False, "a waiting car does not ask again"

    blocking.clear()
    ds._simulated_s = 112.5
    assert ds._lane_cleared(v) is True
    assert v.waiting_case is None
    assert ds.assist_outcomes["AC-1"] == {"vin": "VIN-1", "option": "wait_for_removal",
                                          "chosen_at_s": 100.0, "executed": "waited",
                                          "resumed_after_s": 12.5}


def test_a_plant_car_ahead_does_not_keep_a_waiting_car_waiting():
    ds, v, _car = _backend()
    queued = SimpleNamespace(id=3, type_id="vehicle.lincoln.mkz_2020", attributes={"role_name": "VIN-2"})
    ds.vehicles["VIN-2"] = _Vehicle(vin="VIN-2", actor=None, agent=None, location="gate",
                                    mission=SimpleNamespace(mission_id="M-2"))
    ds._obstacle_ahead = lambda v: (queued, 8.0)
    v.waiting_case = "AC-1"
    assert ds._lane_cleared(v) is True


def test_a_pass_hands_behavior_agent_a_plan_at_3_kmh_and_measures_it():
    ds, v, car = _backend()
    v.assist_obstacle = (BOX.id, BOX.get_location())
    details = {"lane": -1, "own_lane": -2, "kmh": 3.0, "obstacle_at_m": 9.0, "turn_back_m": 15.0,
               "rejoin_m": 22.0, "origin": (0.0, 0.0), "forward": (1.0, 0.0)}
    ds._detour_plan = lambda v, where, option: (["wp1", "wp2"], dict(details))
    ds._attach_collision = lambda v, into: None

    asyncio.run(ds.send(AssistChoose(case_id="AC-1", vin="VIN-1", option="pass_left_3kmh",
                                     chosen_by="supervisor-ana")))
    assert v.agent.plans == [["wp1", "wp2"]]
    assert v.agent._behavior.max_speed == 3.0
    assert v.detour["obstacle_id"] == BOX.id and v.waiting_case is None

    # alongside the box, one lane over, at walking pace
    car.x, car.y, car.kmh = 9.0, -3.5, 3.1
    ds._follow_detour(v)
    v.detour["contacts"].append("static.prop.box01")
    assert v.detour is not None
    # back in its lane past the box
    car.x, car.y, car.kmh = 21.5, 0.0, 3.0
    ds._simulated_s = 130.0
    ds._follow_detour(v)

    assert v.detour is None
    assert v.agent._behavior.max_speed == 8.0, "the mission's speed is restored"
    out = ds.assist_outcomes["AC-1"]
    assert out["executed"] == "pass_left_3kmh" and out["result"] == "back in lane"
    assert out["alongside_lateral_m"] == 3.5 and out["max_kmh_alongside"] == 3.1
    assert out["min_centre_distance_m"] == 3.5 and out["duration_s"] == 30.0
    assert out["contacts"] == ["static.prop.box01"], "contact is reported, not hidden"


def test_an_option_that_cannot_be_driven_leaves_the_car_waiting():
    ds, v, _car = _backend()
    v.assist_obstacle = (BOX.id, BOX.get_location())
    ds._detour_plan = lambda v, where, option: (None, "lane C is not beside the car's lane")

    ds._execute_option(v, "AC-1", "reroute_lane_c")

    assert v.detour is None and v.waiting_case == "AC-1"
    assert ds.assist_outcomes["AC-1"]["not_possible"] == "lane C is not beside the car's lane"


def test_only_drivable_options_are_offered():
    ds, v, _car = _backend()
    ds._detour_plan = lambda v, where, option: ((["wp"], {}) if option == "pass_left_3kmh"
                                                else (None, "too close"))
    assert ds._executable_options(v, BOX.get_location()) == ["wait_for_removal", "pass_left_3kmh"]


def test_the_car_brakes_for_a_prop_in_its_path_but_not_beside_it():
    beside = _actor(8, "walker.pedestrian.0001", 6.0, 2.6)
    parked = _actor(9, "vehicle.lincoln.mkz_2020", 5.0, 0.0)
    ds, v, car = _backend([BOX, beside, parked])
    car.kmh = 8.0

    found = ds._object_in_path(v)
    assert found is not None and found[0] is BOX and math.isclose(found[1], 9.0)

    car.x = -5.0                                     # 14 m away: beyond the stopping distance
    ds._simulated_s += 0.05
    assert ds._object_in_path(v) is None

    car.x = 0.0
    ds._simulated_s += 0.05
    v.detour = {"obstacle_id": BOX.id}               # the object a detour drives round
    assert ds._object_in_path(v) is None


# ---------- slot to slot along the shoulder (yard re-slotting) ----------

class _Loc(_Vec):
    def distance(self, other):
        return math.hypot(self.x - other.x, self.y - other.y)


def _slot_backend(car_x=0.0, others=()):
    """Slots S1 at x=0 and S2 at x=10 on a shoulder running +x; the car parked in S1."""
    ds, v, car = _backend()
    car.x = car_x
    car.get_location = lambda: _Loc(x=car.x, y=0.0, z=0.0)
    car.get_transform = lambda: SimpleNamespace(location=car.get_location(),
                                                get_forward_vector=lambda: _Vec(x=1.0, y=0.0, z=0.0))
    spots = {"S1": 0.0, "S2": 10.0, "LANE": 20.0}
    ds.map = SimpleNamespace(points={
        "S1": SimpleNamespace(carla={"pull_over": True}),
        "S2": SimpleNamespace(carla={"pull_over": True}),
        "LANE": SimpleNamespace(carla={}),
    })
    ds.transform = lambda point: SimpleNamespace(location=_Loc(x=spots[point], y=0.0, z=0.0))
    vehicles = [car] + [SimpleNamespace(id=10 + i, get_location=lambda x=x, y=y: _Vec(x=x, y=y, z=0.0))
                        for i, (x, y) in enumerate(others)]
    ds.world = SimpleNamespace(get_actors=lambda: SimpleNamespace(filter=lambda pattern: vehicles))
    v.location = "S1"
    return ds, v


def test_a_slot_ahead_on_a_clear_shoulder_is_reached_along_the_shoulder():
    ds, v = _slot_backend()
    assert ds._shoulder_move(v, "S2") == "forward"


def test_the_slot_a_car_is_already_in_needs_no_drive():
    ds, v = _slot_backend()
    assert ds._shoulder_move(v, "S1") == "here"


def test_a_slot_behind_is_reversed_onto_and_one_past_a_parked_car_is_routed_by_the_lane():
    ds, v = _slot_backend(car_x=0.0)
    ds.transform = (lambda f: (lambda point: SimpleNamespace(
        location=_Loc(x={"S1": 0.0, "S2": -10.0}[point], y=0.0, z=0.0))))(ds.transform)
    assert ds._shoulder_move(v, "S2") == "back", "behind it, on a clear shoulder: reversed onto"

    ds, v = _slot_backend(others=[(5.0, 0.3)])
    assert ds._shoulder_move(v, "S2") is None, "a parked car is in between"

    ds, v = _slot_backend(others=[(5.0, 3.5)])
    assert ds._shoulder_move(v, "S2") == "forward", "a car in the lane beside is not in the way"


def test_only_slot_to_slot_moves_use_the_shoulder():
    ds, v = _slot_backend()
    assert ds._shoulder_move(v, "LANE") is None
    v.location = "LANE"
    assert ds._shoulder_move(v, "S2") is None
