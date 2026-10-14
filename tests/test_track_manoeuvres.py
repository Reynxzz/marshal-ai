"""The reverse and the brake test are driven on CARLA now, so their pass rules judge measurements.

Until they were driven, the car stood braked through the routine and both steps reported constants:
3.0 m reversed, stopped in 3.4 m. Measured on the server, three runs each: reversed 3.02-3.04 m with
no sideways drift; braked from 20.3-22.2 km/h and stopped in 2.11-2.34 m.
"""

from marshal_ai.driving.routines import TELEMETRY_CHECKS

brake = TELEMETRY_CHECKS["brake_test"]
reverse = TELEMETRY_CHECKS["reverse"]


def test_a_measured_brake_test_passes():
    assert brake({"start_speed_kmh": 20.4, "stopping_distance_m": 2.12})


def test_a_brake_test_that_never_got_up_to_speed_cannot_pass_by_stopping_short():
    assert not brake({"start_speed_kmh": 9.0, "stopping_distance_m": 0.8})


def test_a_long_stop_fails():
    assert not brake({"start_speed_kmh": 20.0, "stopping_distance_m": 6.5})


def test_a_measured_reverse_passes():
    assert reverse({"gear": "R", "distance_m": 3.02, "lateral_m": 0.0})


def test_a_reverse_that_drifts_sideways_fails():
    """The design's check is "moves back straight"."""
    assert not reverse({"gear": "R", "distance_m": 3.0, "lateral_m": 0.9})


def test_a_reverse_that_stops_short_fails():
    assert not reverse({"gear": "R", "distance_m": 1.5, "lateral_m": 0.0})
