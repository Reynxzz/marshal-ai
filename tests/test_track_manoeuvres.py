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


def test_full_lock_is_driven_only_where_there_is_room():
    """Town05's circle would cross the other carriageway; the plant world has paving beside the track."""
    from types import SimpleNamespace

    from marshal_ai.driving.carla_backend import CarlaDrivingSystem, _Vehicle

    class V3(SimpleNamespace):
        def __sub__(self, o):
            return V3(x=self.x - o.x, y=self.y - o.y)

    def backend(map_name, actors=()):
        ds = CarlaDrivingSystem.__new__(CarlaDrivingSystem)
        ds.world = SimpleNamespace(get_map=lambda: SimpleNamespace(name=map_name),
                                   get_actors=lambda: list(actors))
        car = SimpleNamespace(id=1, get_transform=lambda: SimpleNamespace(
            location=V3(x=0.0, y=0.0), get_forward_vector=lambda: V3(x=1.0, y=0.0),
            get_right_vector=lambda: V3(x=0.0, y=1.0)))
        return ds, _Vehicle(vin="VIN-1", actor=car, agent=None, location="test_track")

    ds, v = backend("Carla/Maps/Town05")
    assert ds._room_for_full_lock(v) is False
    ds, v = backend("Carla/Maps/OpenDriveMap")
    assert ds._room_for_full_lock(v) is True
    worker = SimpleNamespace(id=2, type_id="walker.pedestrian.0001", get_location=lambda: V3(x=2.0, y=-3.0))
    ds, v = backend("Carla/Maps/OpenDriveMap", [worker])
    assert ds._room_for_full_lock(v) is False, "a person in the turning area"
