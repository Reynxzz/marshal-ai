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
    # Whether one frame can show the expected result, or whether the step needs the run of
    # frames the design calls a 3 s clip. Judging a sequence from a single still does not just
    # fail to help: asked whether one frame showed a car stopping in a straight line, Gemini
    # answered no at confidence 1.0, which failed a healthy car.
    still_confirmable: bool = True
    # Whether the simulation physically performs this step at all. The car is held stationary
    # for the length of a routine step, so the brake test and the full-lock turn do not happen:
    # their telemetry is scripted and there is nothing for a camera to see. The design scopes
    # this too - it says steps 1 to 4 can be demonstrated in CARLA - so these are recorded as
    # telemetry-only rather than as steps the camera failed to confirm.
    camera_demonstrable: bool = True


R12 = [
    RoutineStep(1, "headlights", "Headlights on",
                "Both headlights lit", ("headlight_left", "headlight_right"),
                {"headlights": "ON"}),
    RoutineStep(2, "indicators", "Left, then right indicator",
                # Checked against the vehicle with each light state set on its own: RightBlinker
                # lights one amber segment of the rear bar, and LeftBlinker renders nothing at all
                # - the frame is identical to lights-off. One side of this asset is simply not
                # drawn, so "left, then right" cannot be read from a camera however it is framed.
                # Telemetry-only here, for the same reason horn and wipers are out of scope.
                "The left indicator lights, then the right one does",
                ("indicator_left", "indicator_right"),
                {"indicator_left": "BLINK", "indicator_right": "BLINK"}, 10.0,
                still_confirmable=False, camera_demonstrable=False),
    RoutineStep(3, "brake_lights", "Brake lights",
                # This asset has one continuous rear bar and no separate third lamp, and it lights
                # amber rather than red. Asking for "left, right and the third" described a real car
                # and failed this one: with the lamps framed closely enough to read, Gemini rightly
                # said there were no such separate lamps.
                "The full width of the rear light bar is lit",
                ("brake_left", "brake_right", "brake_third"),
                {"brake_signal": True, "brake_lamps": "ON"}),
    RoutineStep(4, "reverse", "Reverse 3 m",
                # The car does not actually roll back: it is held stationary for the length of a
                # step, so asking the camera to see 3 m of movement failed a healthy car. What
                # CARLA does demonstrate is the reverse lamp, and that is what is asked for.
                "The reverse lamp is lit, white, at the back of the car", ("reverse",),
                {"gear": "R", "distance_m": 3.0, "reverse_lamp": "ON"}, 10.0),
    RoutineStep(5, "brake_test", "Brake test from 20 km/h",
                "Car stops in a straight line within the marked 5 m zone", (),
                {"start_speed_kmh": 20.0, "stopping_distance_m": 3.4}, 12.0,
                still_confirmable=False, camera_demonstrable=False),
    RoutineStep(6, "full_lock", "Full-lock turn left and right",
                "Car tracks the painted circle left, then right", (),
                {"steering_angle_left_deg": 540.0, "steering_angle_right_deg": 540.0}, 15.0,
                still_confirmable=False, camera_demonstrable=False),
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
