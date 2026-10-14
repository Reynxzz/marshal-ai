"""Flow 2's trigger: the driving system must ask for help when it is actually blocked.

These exercise the decision logic with fakes, so they need no CARLA. The rule that matters is
that a stall only becomes an assist.request when something is in front of the car: a car
stopped for its own reasons must not raise one.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from marshal_ai.driving import carla_backend
from marshal_ai.driving.carla_backend import ASSIST_OPTIONS, _Vehicle
from marshal_ai.flows.assistance import AssistanceDesk
from marshal_ai.llm import Media


@pytest.fixture(autouse=True)
def _stub_carla(monkeypatch):
    """The CARLA wheel is Linux-only and lives on the GPU host; only VehicleControl is needed."""
    if carla_backend.carla is None:
        monkeypatch.setattr(carla_backend, "carla",
                            SimpleNamespace(VehicleControl=lambda **kw: kw))


class _Backend:
    """Only the parts of CarlaDrivingSystem that _check_blocked and raise_assist touch."""

    from marshal_ai.driving.carla_backend import CarlaDrivingSystem as _Real

    _check_blocked = _Real._check_blocked
    raise_assist = _Real.raise_assist
    assist_media = _Real.assist_media
    _is_plant_car = _Real._is_plant_car

    def __init__(self, speed_kmh: float, obstacle=None, distance: float = 4.0):
        self.fixed_delta_s = 1.0
        self.blocked_after_s = 3.0
        self.blocked_speed_kmh = 0.6
        self.obstacle_range_m = 14.0
        self.assist_options = list(ASSIST_OPTIONS)
        self.auto_assist = True
        self.assist_clips: dict[str, list[bytes]] = {}
        self.stored: list[tuple[str, bytes]] = []
        self.events: list = []
        self._handlers = [self.events.append]
        self._obstacle = (obstacle, distance)
        velocity = SimpleNamespace(x=speed_kmh / 3.6, y=0.0, z=0.0)
        self._actor = SimpleNamespace(
            get_velocity=lambda: velocity,
            apply_control=lambda control: None,
        )
        self.vehicles = {"VIN-1": _Vehicle(vin="VIN-1", actor=self._actor, agent=None,
                                          location="finishing")}

    def _obstacle_ahead(self, v):
        return self._obstacle

    def _executable_options(self, v, where):
        return list(self.assist_options)

    def assist_clip(self, v):
        # the seconds before the stop, oldest frame first
        return [b"approaching", b"closer", b"forward-frame"]

    def store_artifact(self, uri, data):
        self.stored.append((uri, data))
        return uri.replace("gs://marshal/", "gs://marshal-artifacts/")


def _vehicle(backend):
    return backend.vehicles["VIN-1"]


def _walker():
    return SimpleNamespace(id=99, type_id="walker.pedestrian.0001",
                           get_location=lambda: SimpleNamespace(x=3.4, y=0.0, z=0.0))


def test_a_moving_car_never_asks_for_help():
    backend = _Backend(speed_kmh=8.0)
    v = _vehicle(backend)

    for _ in range(10):
        assert asyncio.run(backend._check_blocked(v)) is False

    assert v.stalled_s == 0.0
    assert backend.events == []


def test_a_car_stopped_with_nothing_ahead_does_not_ask():
    """BehaviorAgent stops for its own reasons too; only an obstacle justifies a request."""
    backend = _Backend(speed_kmh=0.0, obstacle=None)
    v = _vehicle(backend)

    for _ in range(10):
        assert asyncio.run(backend._check_blocked(v)) is False

    assert backend.events == []


def test_a_car_blocked_by_a_person_asks_after_the_grace_period():
    walker = _walker()
    backend = _Backend(speed_kmh=0.0, obstacle=walker, distance=3.4)
    v = _vehicle(backend)

    assert asyncio.run(backend._check_blocked(v)) is False   # 1 s stalled
    assert asyncio.run(backend._check_blocked(v)) is False   # 2 s
    assert asyncio.run(backend._check_blocked(v)) is True    # 3 s: blocked_after_s

    [request] = backend.events
    assert request.vin == "VIN-1"
    assert request.options == ASSIST_OPTIONS
    assert "a person" in request.reason and "3.4 m" in request.reason
    assert backend.stored[-1][1] == b"forward-frame", "the stored frame is the one at the stop"
    assert v.assist_case == request.case_id, "the car must stay stopped until a human answers"
    # the frame is stored and the request carries the stored URI, not the placeholder
    assert backend.stored == [(f"gs://marshal/assist/{request.case_id}.jpg", b"forward-frame")]
    assert request.clip_uri == f"gs://marshal-artifacts/assist/{request.case_id}.jpg"


def test_the_captured_clip_is_offered_to_the_desk_as_bytes():
    walker = _walker()
    backend = _Backend(speed_kmh=0.0, obstacle=walker)
    v = _vehicle(backend)
    for _ in range(3):
        asyncio.run(backend._check_blocked(v))
    [request] = backend.events

    media = backend.assist_media(request.case_id)

    assert [m.data for m in media] == [b"approaching", b"closer", b"forward-frame"], \
        "the design asks for the seconds before the stop, in order"
    assert all(m.mime == "image/jpeg" for m in media)
    assert "the seconds before the car stopped" in media[0].label


def test_only_one_request_per_blockage():
    walker = _walker()
    backend = _Backend(speed_kmh=0.0, obstacle=walker)
    v = _vehicle(backend)
    for _ in range(12):
        asyncio.run(backend._check_blocked(v))

    assert len(backend.events) == 1, "assist_case set means the car is already waiting"


def test_auto_assist_can_be_turned_off():
    walker = _walker()
    backend = _Backend(speed_kmh=0.0, obstacle=walker)
    backend.auto_assist = False
    v = _vehicle(backend)

    for _ in range(10):
        assert asyncio.run(backend._check_blocked(v)) is False
    assert backend.events == []


def test_the_desk_prefers_real_bytes_over_a_bare_uri():
    from marshal_ai.driving import AssistRequest

    frame = Media(mime="image/jpeg", data=b"real", label="forward view at the stop")
    rt = SimpleNamespace(driving=SimpleNamespace(assist_media=lambda case_id: frame),
                         world=None)
    desk = AssistanceDesk.__new__(AssistanceDesk)
    desk.rt = rt

    media = desk.clip(AssistRequest(case_id="AC-1", vin="VIN-1", location="finishing",
                                    reason="object", options=ASSIST_OPTIONS,
                                    clip_uri="gs://marshal/assist/AC-1.jpg"))

    assert [m.data for m in media] == [b"real"], "a gs:// URI alone cannot be read by Vertex AI"


def test_the_desk_still_works_when_the_backend_has_no_frame():
    from marshal_ai.driving import AssistRequest

    rt = SimpleNamespace(driving=SimpleNamespace(assist_media=lambda case_id: None), world=None)
    desk = AssistanceDesk.__new__(AssistanceDesk)
    desk.rt = rt

    media = desk.clip(AssistRequest(case_id="AC-1", vin="VIN-1", location="finishing",
                                    reason="object", options=ASSIST_OPTIONS, clip_uri=None))

    assert media == []
