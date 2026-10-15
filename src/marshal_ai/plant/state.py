"""Operational state around the cars: trucks, closed zones, rework bay occupancy, line rate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

REWORK_BAYS = ["rework.paint", "rework.body", "rework.electrical", "rework.mechanical"]


@dataclass
class Truck:
    truck_id: str
    scheduled: datetime
    eta: datetime

    @property
    def delay_min(self) -> float:
        return (self.eta - self.scheduled) / timedelta(minutes=1)


@dataclass
class PlantState:
    trucks: dict[str, Truck] = field(default_factory=dict)
    closed_zones: set[str] = field(default_factory=set)
    # zone or map point -> what is going on there that a car's route should know about, e.g. "forklift unloading
    # pallets", "two people crossing". Reported by supervisors or plant systems; read by route choice.
    hazards: dict[str, str] = field(default_factory=dict)
    bay_capacity: dict[str, int] = field(default_factory=lambda: {b: 1 for b in REWORK_BAYS})
    bay_occupants: dict[str, list[str]] = field(default_factory=lambda: {b: [] for b in REWORK_BAYS})
    line_rate_per_h: float = 30.0         # cars off the line
    yard_rate_per_h: float = 30.0         # cars the yard can absorb

    def trucks_by_eta(self) -> list[Truck]:
        return sorted(self.trucks.values(), key=lambda t: t.eta)

    def next_truck(self) -> Truck | None:
        trucks = self.trucks_by_eta()
        return trucks[0] if trucks else None

    def bay_free(self, bay: str) -> bool:
        return len(self.bay_occupants.get(bay, [])) < self.bay_capacity.get(bay, 1)

    def occupy(self, bay: str, vin: str) -> None:
        self.bay_occupants.setdefault(bay, []).append(vin)

    def release(self, vin: str) -> None:
        for occupants in self.bay_occupants.values():
            if vin in occupants:
                occupants.remove(vin)
