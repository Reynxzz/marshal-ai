"""Gemini parks the car (driving/parking.py): geometry, the session's rules, and the CARLA tick."""

import asyncio
import math
from types import SimpleNamespace

from marshal_ai.driving import carla_backend, parking
from marshal_ai.driving.parking import (
    OPTIONS, Box, GeminiParker, ParkingSession, Pose, advance, is_parked, kerb, overhang, prompt,
    render, to_slot_frame,
)
from marshal_ai.eval.parking import Case, KinematicCar, obstacles_for, planner_decide, run_case
from marshal_ai.llm import decision_schema
from marshal_ai.offline import offline_llm

Decision = decision_schema(*OPTIONS)


def _decision(choice):
    return Decision(choice=choice, probs={o: (1.0 if o == choice else 0.0) for o in OPTIONS})


def test_slot_frame_from_carla_pose():
    # A slot facing CARLA yaw 90 (+y). CARLA is left-handed: +x is then to the slot's left.
    ahead = to_slot_frame(0, 0, 90, 0, 2, 90)
    assert math.isclose(ahead.x, 2.0) and abs(ahead.y) < 1e-9 and abs(ahead.yaw) < 1e-9
    left = to_slot_frame(0, 0, 90, 1.5, 0, 90)
    assert math.isclose(left.y, 1.5) and math.isclose(left.x, 0.0, abs_tol=1e-9)
    # yaw a little clockwise in CARLA (to the right) is negative in the slot frame
    assert to_slot_frame(0, 0, 90, 0, 0, 100).yaw_deg < 0


def test_parked_means_inside_the_lines_and_nearly_straight():
    assert is_parked(Pose(0.0, 0.0, 0.0))
    assert is_parked(Pose(0.2, 0.3, math.radians(6)))
    assert not is_parked(Pose(0.0, 0.0, math.radians(10)))
    over = overhang(Pose(0.0, 1.2, 0.0))
    assert over["lane line"] > 0.3 and over["kerb"] == 0.0
    assert not is_parked(Pose(0.6, 0.0, 0.0))                      # nose over the front line


def test_wheel_turns_the_nose_the_way_a_driver_expects():
    assert advance(Pose(0, 0, 0), 1, 1, 1.0).yaw > 0                # forward, wheel left: nose left
    assert advance(Pose(0, 0, 0), -1, 1, 1.0).yaw < 0               # reverse, wheel left: nose right


def test_parked_is_refused_outside_the_slot_and_ends_the_session_inside():
    s = ParkingSession("V1", "Y4")
    s.apply(_decision("parked"), Pose(-3.0, 2.0, 0.0))
    assert not s.done and s.refused == 1 and "not parked yet" in s.note
    s.apply(_decision("parked"), Pose(0.1, 0.0, 0.0))
    assert s.done and s.outcome == "parked"


def test_ask_a_person_and_running_out_of_moves_end_the_session():
    s = ParkingSession("V1", "Y4")
    s.apply(_decision("ask_a_person"), Pose(-8, 3.5, 0))
    assert s.outcome == "asked"
    s = ParkingSession("V1", "Y4", max_decisions=2)
    for _ in range(2):
        s.apply(_decision("forward_straight"), Pose(-8, 3.5, 0))
        s.phase = "decide"
    assert not s.needs_decision and s.outcome == "gave_up"


def test_safety_check_stops_a_move_into_the_car_ahead_without_touching_it():
    ahead = Box(5.0, 0.0, 0.0, 4.9, 1.9, "the car ahead")     # 0.9 m beyond the bumper
    car = KinematicCar(Pose(-0.8, 0.0, 0.0))
    s = ParkingSession("V1", "Y4")
    for _ in range(3):                                              # keep trying to drive into it
        s.apply(_decision("forward_straight"), car.pose)
        while s.phase != "decide":
            car.step(s.control(car.pose, [ahead], car.speed, 0.05), 0.05)
            assert parking.touching(car.pose, [ahead]) is None
    assert s.safety_stops >= 1 and "the car ahead" in s.note


def test_moves_prompt_and_view_describe_the_car_in_words_and_pixels():
    s = ParkingSession("VIN-P3", "Y7", mode="moves")
    view = s.view(Pose(-6.0, 3.5, math.radians(-10)), [kerb()])
    text = prompt(view)
    assert "6.0 m short of the slot's centre" in text
    assert "slot is to the car's right" in text and "10 degrees to the right" in text
    assert render(view)[:4] == b"\x89PNG"


def test_offline_stand_in_parks_a_car_through_the_decision_interface():
    parker = GeminiParker(offline_llm())
    result = run_case(Case("none-run8", "none", -8.0), parker.decide)
    assert result["outcome"] == "parked" and result["collided"] is None
    assert offline_llm().handlers["drive.park"]                      # registered for MARSHAL_LLM=offline


def test_paths_are_offered_drawn_and_described():
    s = ParkingSession("VIN-P3", "Y7")
    view = s.view(Pose(-8.0, 3.5, 0.0), [kerb()])
    assert view.paths and view.options[0] == "path_A" and view.options[-1] == "ask_a_person"
    text = prompt(view)
    assert "path_A:" in text and "change" in text and ("closest" in text or "never within" in text)
    assert render(view)[:4] == b"\x89PNG"


def test_choosing_a_path_drives_its_first_segment_then_asks_again():
    s = ParkingSession("V1", "Y4")
    car = KinematicCar(Pose(-8.0, 3.5, 0.0))
    view = s.view(car.pose, [kerb()])
    choose = decision_schema(*view.options)(choice="path_A", probs={o: float(o == "path_A") for o in view.options})
    s.apply(choose, car.pose, view=view)
    segment = s.trace[-1]["segment"]
    assert 1 <= len(segment) <= 3
    while s.phase != "decide":
        car.step(s.control(car.pose, [kerb()], car.speed, 0.05), 0.05)
    assert s.moves == segment and car.pose.x > -8.0 + 0.5 * len(segment)


def test_a_slot_between_two_cars_ends_with_a_person_not_a_collision():
    result = run_case(Case("both-run8", "both", -8.0), planner_decide)
    assert result["outcome"] == "asked" and result["decisions"] == 0      # no path: a person decides
    forced = run_case(Case("both-run8", "both", -8.0), lambda view, image=None: _decision("forward_right"),
                      mode="moves")
    assert forced["collided"] is None and forced["outcome"] in ("gave_up", "asked")
    assert forced["safety_stops"] >= 1


# ---------- the CARLA backend's tick, against a stub car ----------

class _V3(SimpleNamespace):
    def distance(self, other):
        return math.hypot(self.x - other.x, self.y - other.y)


class _Transform:
    def __init__(self, x, y, yaw):
        self.location = _V3(x=x, y=y, z=0.5)
        self.rotation = SimpleNamespace(yaw=yaw)

    def get_forward_vector(self):
        r = math.radians(self.rotation.yaw)
        return _V3(x=math.cos(r), y=math.sin(r), z=0.0)


class _Car:
    """Moves like the eval's kinematic car, driven by the VehicleControl it is given."""

    id = 1
    type_id = "vehicle.test"

    def __init__(self, pose):
        self.sim = KinematicCar(pose)       # slot at the CARLA origin facing yaw 0: y_left = -carla y

    def get_transform(self):
        p = self.sim.pose
        return _Transform(p.x, -p.y, -math.degrees(p.yaw))

    def get_velocity(self):
        f = self.get_transform().get_forward_vector()
        return _V3(x=f.x * self.sim.speed, y=f.y * self.sim.speed, z=0.0)

    def apply_control(self, c):
        self.control = c


def _control(throttle=0.0, brake=0.0, steer=0.0, reverse=False):
    return SimpleNamespace(throttle=throttle, brake=brake, steer=steer, reverse=reverse)


def test_carla_backend_parks_with_gemini_decisions(monkeypatch):
    monkeypatch.setattr(carla_backend, "carla", SimpleNamespace(VehicleControl=_control))
    system = object.__new__(carla_backend.CarlaDrivingSystem)
    system.transform = lambda point: _Transform(0.0, 0.0, 0.0)
    system.world = SimpleNamespace(get_actors=lambda: [])
    system.fixed_delta_s = 0.05
    system.parking_driver = GeminiParker(offline_llm())
    system.parking_log = {}
    notes = []
    system.park_note = notes.append
    car = _Car(Pose(-8.0, 3.5, 0.0))
    v = carla_backend._Vehicle(vin="VIN-P1", actor=car, agent=None, location="finishing")

    async def drive():
        for _ in range(20000):
            if system._gemini_park(v, "yard.Y4"):
                return True
            await asyncio.sleep(0)
            c = car.control
            wheel = -c.steer / carla_backend.CARLA_PARK_STEER
            speed = 0.0 if c.brake >= 0.5 else (-0.6 if c.reverse else 0.7) if c.throttle > 0 or abs(car.sim.speed) > 0 else 0.0
            car.sim.step(parking.Command(speed, wheel, c.brake >= 0.5), 0.05)
        return False

    assert asyncio.run(drive())
    entry = system.parking_log["VIN-P1"][0]
    assert entry["outcome"] == "parked" and entry["decisions"] >= 3
    assert is_parked(car.sim.pose) and v.parking is None
    assert notes and notes[0].startswith("VIN-P1 parking Y4: path A")
