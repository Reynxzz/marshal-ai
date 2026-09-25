"""Mission API messages: the only way Marshal talks to the driving system.

Marshal -> driving system: mission.go, mission.hold, mission.resume, assist.choose
Driving system -> Marshal: status (1 Hz), assist.request, mission.done, track.step

There is deliberately no message for steering, braking, accelerating or
overriding the driving system's own safety stops.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from marshal_ai.records import now


class _Msg(BaseModel):
    at: datetime = Field(default_factory=now)


# Marshal -> driving system

class MissionGo(_Msg):
    type: Literal["mission.go"] = "mission.go"
    mission_id: str
    vin: str
    waypoints: list[str]                    # plant map points, visited in order
    max_speed_kmh: float = 10.0
    routine: str | None = None              # test track routine id, e.g. R-12, run at test_track
    routine_steps: list[int] | None = None  # None: every step; re-inspection repeats failed steps only


class MissionHold(_Msg):
    type: Literal["mission.hold"] = "mission.hold"
    vin: str
    duration_s: float | None = None         # None: until mission.resume
    reason: str = ""


class MissionResume(_Msg):
    type: Literal["mission.resume"] = "mission.resume"
    vin: str


class AssistChoose(_Msg):
    type: Literal["assist.choose"] = "assist.choose"
    case_id: str
    vin: str
    option: str                             # must be one of the request's options
    chosen_by: str                          # the human who decided


Command = Annotated[
    Union[MissionGo, MissionHold, MissionResume, AssistChoose], Field(discriminator="type")
]


# Driving system -> Marshal

class Status(_Msg):
    type: Literal["status"] = "status"
    vin: str
    location: str | None                    # last map point passed
    x: float
    y: float
    speed_kmh: float
    mission_id: str | None
    progress: float                         # 0..1 along the current mission
    held: bool = False
    stopped_for_assist: bool = False


class AssistRequest(_Msg):
    type: Literal["assist.request"] = "assist.request"
    case_id: str
    vin: str
    location: str | None
    reason: str                             # e.g. "unknown object in lane"
    options: list[str]
    clip_uri: str | None = None


class MissionDone(_Msg):
    type: Literal["mission.done"] = "mission.done"
    mission_id: str
    vin: str
    location: str


class TrackStep(_Msg):
    """One routine step performed on the test track, with what the car's electronics report."""

    type: Literal["track.step"] = "track.step"
    vin: str
    mission_id: str
    routine: str
    step: int
    name: str
    telemetry: dict


Event = Annotated[
    Union[Status, AssistRequest, MissionDone, TrackStep], Field(discriminator="type")
]

command_adapter: TypeAdapter[Command] = TypeAdapter(Command)
event_adapter: TypeAdapter[Event] = TypeAdapter(Event)
