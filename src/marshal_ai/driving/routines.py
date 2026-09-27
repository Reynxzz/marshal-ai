"""Test track routines (flow 4). The driving system performs them; Marshal only names one."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoutineStep:
    step: int
    name: str
    command: str
    expected_on_camera: str
    lamps: tuple[str, ...] = ()          # lamps the camera must see lit
    telemetry: dict = field(default_factory=dict)   # what healthy electronics report
    duration_s: float = 8.0
    # Whether one frame can show the expected result. A blinker is lit for only part of its
    # cycle, and a stopping distance or a turning circle is a path rather than a state, so a
    # still cannot confirm those however good the model is - the design calls for a 3 s clip
    # for exactly this reason. Judging them from a still does not just fail to help: asked
    # whether a still showed a car stopping in a straight line, Gemini answered no at
    # confidence 1.0, which failed a healthy car.
    still_confirmable: bool = True


R12 = [
    RoutineStep(1, "headlights", "Headlights on",
                "Both headlights lit", ("headlight_left", "headlight_right"),
                {"headlights": "ON"}),
    RoutineStep(2, "indicators", "Left, then right indicator",
                "Left indicator blinks, then right indicator blinks; correct side each time",
                ("indicator_left", "indicator_right"),
                {"indicator_left": "BLINK", "indicator_right": "BLINK"}, 10.0,
                still_confirmable=False),
    RoutineStep(3, "brake_lights", "Brake lights",
                "All brake lights lit, left, right and the third (centre high) lamp",
                ("brake_left", "brake_right", "brake_third"),
                {"brake_signal": True, "brake_lamps": "ON"}),
    RoutineStep(4, "reverse", "Reverse 3 m",
                "Reverse light on and the car moves straight back about 3 m", ("reverse",),
                {"gear": "R", "distance_m": 3.0, "reverse_lamp": "ON"}, 10.0),
    RoutineStep(5, "brake_test", "Brake test from 20 km/h",
                "Car stops in a straight line within the marked 5 m zone", (),
                {"start_speed_kmh": 20.0, "stopping_distance_m": 3.4}, 12.0,
                still_confirmable=False),
    RoutineStep(6, "full_lock", "Full-lock turn left and right",
                "Car tracks the painted circle left, then right", (),
                {"steering_angle_left_deg": 540.0, "steering_angle_right_deg": 540.0}, 15.0,
                still_confirmable=False),
]

ROUTINES = {"R-12": R12}

# Telemetry limits checked in code, not by the model
TELEMETRY_CHECKS = {
    "headlights": lambda t: t.get("headlights") == "ON",
    "indicators": lambda t: t.get("indicator_left") == "BLINK" and t.get("indicator_right") == "BLINK",
    "brake_lights": lambda t: t.get("brake_signal") is True and t.get("brake_lamps") == "ON",
    "reverse": lambda t: t.get("gear") == "R" and abs(t.get("distance_m", 0) - 3.0) <= 0.5,
    "brake_test": lambda t: t.get("stopping_distance_m", 99) <= 5.0,
    "full_lock": lambda t: min(t.get("steering_angle_left_deg", 0), t.get("steering_angle_right_deg", 0)) >= 500,
}


def routine_steps(routine: str, only: list[int] | None = None) -> list[RoutineStep]:
    steps = ROUTINES[routine]
    return [s for s in steps if only is None or s.step in only]
