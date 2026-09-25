"""Ground truth for simulated cars, and the cameras that see it.

The fake driving system reports telemetry the way a car's electronics would: a dead
lamp still reports ON. Only the cameras see the truth, which is what lets the
Functional Checker catch it (flow 4). In CARLA the same roles are played by real
sensors; see marshal_ai.cameras for the protocols.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from marshal_ai.llm import Media

GATE_VIEWS = {
    "front": ["front", "bonnet"],
    "rear": ["rear", "boot"],
    "left": ["front_left_door", "rear_left_door"],
    "right": ["front_right_door", "rear_right_door"],
}


@dataclass
class Defect:
    zone: str
    type: str
    size_cm: float | None = None


@dataclass
class CarTruth:
    colour: str
    model: str = "sedan"
    trim: str = "standard"
    fitted: list[str] = field(default_factory=list)       # options actually fitted
    defects: list[Defect] = field(default_factory=list)
    dead_lamps: set[str] = field(default_factory=set)     # e.g. {"brake_left"}

    def lamp_ok(self, lamp: str) -> bool:
        return lamp not in self.dead_lamps


@dataclass
class FakeWorld:
    cars: dict[str, CarTruth] = field(default_factory=dict)
    scenes: dict[str, dict] = field(default_factory=dict)   # assist case id -> what the clip shows

    def add(self, vin: str, truth: CarTruth) -> None:
        self.cars[vin] = truth

    def repair(self, vin: str, defect_types: list[str] | None = None) -> None:
        """Rework fixes the car: defects of the given types (all if None), dead lamps if electrical."""
        car = self.cars[vin]
        if defect_types is None:
            car.defects.clear()
            car.dead_lamps.clear()
            return
        car.defects = [d for d in car.defects if d.type not in defect_types]
        if any(t in defect_types for t in ("brake_light", "lamp", "indicator", "headlight", "electrical")):
            car.dead_lamps.clear()


class FakeGateCamera:
    def __init__(self, world: FakeWorld):
        self.world = world

    def capture(self, vin: str, views: list[str] | None = None) -> list[Media]:
        truth = self.world.cars[vin]
        out = []
        for view, zones in GATE_VIEWS.items():
            if views is not None and view not in views:
                continue
            out.append(Media(
                mime="image/jpeg", uri=f"gs://marshal/gate/{vin}/{view}.jpg", label=f"{view} camera",
                truth={
                    "view": view, "zones": zones, "colour": truth.colour, "model": truth.model,
                    "trim": truth.trim, "fitted": list(truth.fitted),
                    "defects": [vars(d) for d in truth.defects if d.zone in zones],
                },
            ))
        return out


class FakeTrackCamera:
    def __init__(self, world: FakeWorld):
        self.world = world

    def clip(self, vin: str, step: int, name: str) -> Media:
        truth = self.world.cars[vin]
        return Media(
            mime="video/mp4", uri=f"gs://marshal/track/{vin}/step{step}_{name}.mp4",
            label=f"track camera, step {step} {name}",
            truth={"step": name, "dead_lamps": sorted(truth.dead_lamps)},
        )
