"""Scenario spec: the JSON Gemini writes instead of simulator code (flow 6).

Named map points instead of coordinates, a closed list of actor types and explicit
pass criteria. The Adversarial Tester varies these fields directly (flow 7).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PLANT_SPEED_LIMIT_KMH = 10.0


class TimeOfDay(str, Enum):
    NOON = "noon"
    DUSK = "dusk"
    NIGHT = "night"


class Weather(str, Enum):
    CLEAR = "clear"
    RAIN = "rain"
    FOG = "fog"


class ActorType(str, Enum):
    WORKER = "worker"
    FORKLIFT = "forklift"
    PARKED_CAR = "parked_car"
    PALLET_STACK = "pallet_stack"
    CAR = "car"                      # a second moving car, e.g. braking ahead of ego
    BOX = "box"                      # an unknown object in ego's lane: the car must stop before it


OCCLUDERS = {ActorType.FORKLIFT, ActorType.PARKED_CAR, ActorType.PALLET_STACK}


class Trigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ego_distance_m: float = Field(ge=2, le=30)


class Actor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActorType
    state: Literal["parked", "stationary", "moving", "crossing", "braking"] = "stationary"
    position: str | None = None      # named map point
    start: str | None = None         # named map point, or "behind:<actor type>"
    walk_speed_ms: float | None = Field(default=None, ge=0.5, le=3.0)
    speed_kmh: float | None = Field(default=None, ge=0, le=PLANT_SPEED_LIMIT_KMH)
    trigger: Trigger | None = None


class Ego(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str                       # "a->b" or "a->b->c", named map points
    speed_kmh: float = Field(gt=0, le=PLANT_SPEED_LIMIT_KMH)

    @property
    def waypoints(self) -> list[str]:
        return [p.strip() for p in self.route.split("->")]

    @field_validator("route")
    @classmethod
    def _two_points(cls, v: str) -> str:
        if len([p for p in v.split("->") if p.strip()]) < 2:
            raise ValueError("route needs at least two points, e.g. 'line_end->finishing'")
        return v


# Pass criteria are plain strings so specs stay short and readable:
#   "no_contact" | "reached_goal" | "<metric> <op> <number>"
FLAG_CRITERIA = {"no_contact", "reached_goal"}
METRICS = {
    "min_gap_to_person_m",
    "min_gap_to_vehicle_m",
    "speed_near_person_kmh",
    "max_speed_kmh",
    "time_to_goal_s",
}
_CRITERION = re.compile(r"^\s*([a-z_]+)\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)\s*$")


class Criterion(BaseModel):
    metric: str
    op: Literal["<=", ">=", "<", ">", "flag"]
    value: float | None = None

    def holds(self, metrics: dict[str, float]) -> bool:
        """Evaluate against the metrics of one run. Used by the rule judge."""
        if self.op == "flag":
            return bool(metrics.get(self.metric))
        actual = metrics[self.metric]
        return {"<=": actual <= self.value, ">=": actual >= self.value,
                "<": actual < self.value, ">": actual > self.value}[self.op]


def parse_criterion(text: str) -> Criterion:
    if text.strip() in FLAG_CRITERIA:
        return Criterion(metric=text.strip(), op="flag")
    m = _CRITERION.match(text)
    if not m:
        raise ValueError(
            f"cannot parse criterion {text!r}; use one of {sorted(FLAG_CRITERIA)} "
            f"or '<metric> <op> <number>'"
        )
    metric, op, value = m.groups()
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}; known metrics: {sorted(METRICS)}")
    return Criterion(metric=metric, op=op, value=float(value))


class ScenarioSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^SC-\d{4}(-v\d+)?$")
    source: str
    description: str = ""
    map_zone: str
    time_of_day: TimeOfDay = TimeOfDay.NOON
    weather: Weather = Weather.CLEAR
    ego: Ego
    actors: list[Actor] = Field(default_factory=list, max_length=8)
    pass_criteria: list[str] = Field(min_length=1)

    @field_validator("pass_criteria")
    @classmethod
    def _parse_criteria(cls, v: list[str]) -> list[str]:
        for c in v:
            parse_criterion(c)
        return v

    def criteria(self) -> list[Criterion]:
        return [parse_criterion(c) for c in self.pass_criteria]
