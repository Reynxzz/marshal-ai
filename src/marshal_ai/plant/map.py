"""Plant map: named points, zones and shortest routes between points."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_MAP_PATH = Path(__file__).with_name("plant_map.yaml")


@dataclass(frozen=True)
class MapPoint:
    name: str
    zone: str
    x: float
    y: float
    carla: dict | None = None


class PlantMap:
    def __init__(self, zones: dict[str, str], points: dict[str, MapPoint],
                 edges: dict[str, dict[str, float]], carla_map: str | None = None):
        self.zones = zones
        self.points = points
        self.edges = edges
        self.carla_map = carla_map

    def has_point(self, name: str) -> bool:
        return name in self.points

    def has_zone(self, name: str) -> bool:
        return name in self.zones

    def points_in_zone(self, zone: str) -> list[str]:
        return [p.name for p in self.points.values() if p.zone == zone]

    def distance(self, a: str, b: str) -> float:
        """Straight-line metres between two points, measured where the cars actually are.

        The schematic x/y in plant_map.yaml are a diagram, not a survey, and they ranked the yard
        slots wrongly: "nearest the loading bay" came out as Y7, which is 47 m from the bay in
        CARLA, while Y8 is 7 m from it. Cars for the next truck were being parked across the yard
        from where it loads. So when both points have a CARLA transform, that is what is measured;
        the schematic is the fallback for points that have none.
        """
        pa, pb = self.points[a], self.points[b]
        if pa.carla and pb.carla:
            return math.hypot(pa.carla["x"] - pb.carla["x"], pa.carla["y"] - pb.carla["y"])
        return math.hypot(pa.x - pb.x, pa.y - pb.y)

    def route(self, start: str, goal: str) -> list[str]:
        """Shortest path by segment length. Raises KeyError or ValueError if there is none."""
        for p in (start, goal):
            if p not in self.points:
                raise KeyError(f"unknown map point: {p}")
        dist = {start: 0.0}
        prev: dict[str, str] = {}
        queue = [(0.0, start)]
        while queue:
            d, node = heapq.heappop(queue)
            if node == goal:
                break
            if d > dist[node]:
                continue
            for nxt, length in self.edges.get(node, {}).items():
                nd = d + length
                if nd < dist.get(nxt, math.inf):
                    dist[nxt] = nd
                    prev[nxt] = node
                    heapq.heappush(queue, (nd, nxt))
        if goal not in dist:
            raise ValueError(f"no route from {start} to {goal}")
        path = [goal]
        while path[-1] != start:
            path.append(prev[path[-1]])
        return path[::-1]

    def route_length(self, path: list[str]) -> float:
        return sum(self.edges[a][b] for a, b in zip(path, path[1:]))


def _parse(raw: dict) -> PlantMap:
    zones = {name: z.get("description", "") for name, z in raw["zones"].items()}
    points = {
        name: MapPoint(name=name, zone=p["zone"], x=p["x"], y=p["y"], carla=p.get("carla"))
        for name, p in raw["points"].items()
    }
    for p in points.values():
        if p.zone not in zones:
            raise ValueError(f"point {p.name} is in unknown zone {p.zone}")
    edges: dict[str, dict[str, float]] = {name: {} for name in points}
    for seg in raw["segments"]:
        one_way = isinstance(seg, dict) and seg.get("one_way", False)
        a, b = (seg["from"], seg["to"]) if isinstance(seg, dict) else seg
        for p in (a, b):
            if p not in points:
                raise ValueError(f"segment refers to unknown point {p}")
        length = math.hypot(points[a].x - points[b].x, points[a].y - points[b].y)
        edges[a][b] = length
        if not one_way:
            edges[b][a] = length
    return PlantMap(zones, points, edges, raw.get("carla_map"))


@lru_cache(maxsize=4)
def load_plant_map(path: Path = DEFAULT_MAP_PATH) -> PlantMap:
    return _parse(yaml.safe_load(Path(path).read_text()))
