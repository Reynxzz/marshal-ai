"""Runtime side of compiled Scenic scenarios (flows 6 and 7 on MetaDrive).

`layout()` turns a spec into positions in plant metres (which are Scenic's coordinates for the
plant map). The compiler writes those numbers into the Scenic program, so the program reads as a
plain scene. During the run the program calls back here:

    EgoDriver  the driving system under test: the plant's `Autopilot` with the release's profile
    LeadCar    a car ahead of ego that brakes (actor type "car", state "braking")
    Monitor    measures the metrics the rule judge reads, the same names as the local simulator

Placement follows the local simulator: parked things stand beside the lane on the kerb side, a
worker "behind" something stands just past its far end, and people cross the lane when ego is
within the trigger distance. Actors that would overlap move on along the lane until they are clear.
A box stands in ego's lane; MetaDrive draws it as a parked car proxy too, but it is not tall. MetaDrive only has cars and pedestrians, so forklifts, pallet stacks
and parked cars are parked car proxies; they block line of sight the same way.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from shapely.geometry import Point, Polygon

from marshal_ai.driving.autopilot import (
    CAR_LENGTH_M, CAR_WIDTH_M, Autopilot, LanePath, Pose, Thing, box,
)
from marshal_ai.driving.metadrive_backend import throttle_brake
from marshal_ai.lab.sim import NEAR_PERSON_GAP_M, TIMEOUT_S, DriverProfile
from marshal_ai.lab.spec import ActorType, ScenarioSpec
from marshal_ai.plant import PlantMap, load_plant_map

KERB_LAT = -(1.75 + 0.4 + CAR_WIDTH_M / 2)     # parked things: centre this far right of the lane
WORKER_LAT = -3.0
CROSS_TO_LAT = 5.0                              # crossing actors walk to the far side of the road
MAX_STEER_DEG = 40.0                            # MetaDrive DefaultVehicle
PERSON_RADIUS_M = 0.35


@dataclass
class Placed:
    index: int
    type: str
    x: float
    y: float
    yaw: float                                  # radians from +x
    kind: str                                   # person | vehicle | object
    moves: bool = False
    speed_ms: float = 0.0
    trigger_m: float | None = None
    cross_yaw: float | None = None              # direction of travel when triggered
    brake_at_s: float | None = None             # lead car: time it brakes
    lateral0: float = 0.0
    path_s: float = 0.0


@dataclass
class Layout:
    route: list[str]
    lane: list[tuple[float, float]]
    ego: tuple[float, float, float]
    ego_speed_ms: float
    actors: list[Placed] = field(default_factory=list)
    time_of_day: str = "noon"
    weather: str = "clear"


def ego_route(spec: ScenarioSpec, m: PlantMap) -> list[str]:
    names = [spec.ego.waypoints[0]]
    for a, b in zip(spec.ego.waypoints, spec.ego.waypoints[1:]):
        names += m.route(a, b)[1:]
    return names


def _at(path: LanePath, s: float, lat: float) -> tuple[float, float, float]:
    x, y, yaw = path.point(s)
    return x - math.sin(yaw) * lat, y + math.cos(yaw) * lat, yaw


def layout(spec: ScenarioSpec, m: PlantMap | None = None) -> Layout:
    m = m or load_plant_map()
    route = ego_route(spec, m)
    path = LanePath.through(m, route)
    ex, ey, eyaw = path.point(0.0)
    v = spec.ego.speed_kmh / 3.6
    out = Layout(route, [(round(x, 3), round(y, 3)) for x, y in path.pts], (ex, ey, eyaw), v,
                 time_of_day=spec.time_of_day.value, weather=spec.weather.value)

    taken = [box(ex, ey, eyaw, CAR_LENGTH_M, CAR_WIDTH_M)]

    def clear_spot(s: float, lat: float, person: bool) -> tuple[float, float, float, float]:
        """Move along the lane until the actor overlaps nothing placed before it (Scenic rejects overlaps)."""
        for _ in range(40):
            x, y, yaw = _at(path, s, lat)
            shape = Point(x, y).buffer(PERSON_RADIUS_M) if person else box(x, y, yaw, CAR_LENGTH_M, CAR_WIDTH_M)
            if all(shape.distance(t) > 0.3 for t in taken):
                taken.append(shape)
                return s, x, y, yaw
            s = min(path.length, s + 0.5)
        raise ValueError("no free spot for an actor near its map point")

    occluder_s: dict[ActorType, float] = {}
    for a in spec.actors:
        if a.position and a.type in (ActorType.FORKLIFT, ActorType.PALLET_STACK, ActorType.PARKED_CAR):
            p = m.points[a.position]
            occluder_s[a.type] = path.project(p.x, p.y)

    for i, a in enumerate(spec.actors):
        person = a.type == ActorType.WORKER
        trigger = a.trigger.ego_distance_m if a.trigger else None
        if a.type == ActorType.CAR and a.state == "braking":
            s = min(path.length, (trigger if trigger is not None else 10.0) + CAR_LENGTH_M)
            s, x, y, yaw = clear_spot(s, 0.0, False)
            out.actors.append(Placed(i, a.type.value, x, y, yaw, "vehicle", True, v,
                                     brake_at_s=3.0, path_s=s))
            continue
        if a.start and a.start.startswith("behind:"):
            occ = ActorType(a.start.removeprefix("behind:"))
            s = occluder_s.get(occ, 0.0) + CAR_LENGTH_M / 2 + 0.7
            lat = KERB_LAT
        else:
            p = m.points[a.position or a.start]
            s = path.project(p.x, p.y)
            lat = 0.0 if a.type == ActorType.BOX else WORKER_LAT if person else KERB_LAT
        s, x, y, yaw = clear_spot(s, lat, person)
        moves = a.state in ("moving", "crossing") or (person and (trigger is not None or a.start is not None
                                                                   and a.start.startswith("behind:")))
        speed = (a.walk_speed_ms or 1.3) if person else (a.speed_kmh or 5.0) / 3.6
        out.actors.append(Placed(
            i, a.type.value, x, y, yaw if not person else yaw + math.pi / 2,
            "person" if person else "object" if a.type == ActorType.BOX else "vehicle", moves,
            speed if moves else 0.0,
            (trigger if trigger is not None else 8.0) if moves else None,
            cross_yaw=yaw + math.pi / 2 if moves else None, lateral0=lat, path_s=s))
    return out


def layout_from_json(text: str) -> Layout:
    raw = json.loads(text)
    actors = [Placed(**a) for a in raw.pop("actors")]
    return Layout(**raw, actors=actors,)


def layout_to_json(lay: Layout) -> str:
    return json.dumps(asdict(lay))


# ---------- helpers the Scenic program calls ----------

def scenic_heading(yaw: float) -> float:
    """Scenic headings start at north (+y); plant yaw starts at east (+x)."""
    return yaw - math.pi / 2


def plant_yaw(scenic_heading_: float) -> float:
    return scenic_heading_ + math.pi / 2


def footprint(obj) -> Polygon:
    x, y = float(obj.position[0]), float(obj.position[1])
    if getattr(obj, "marshalKind", "") == "person":
        return Point(x, y).buffer(PERSON_RADIUS_M)
    return box(x, y, plant_yaw(obj.heading), CAR_LENGTH_M, CAR_WIDTH_M)


def _velocity(obj) -> tuple[float, float]:
    vel = obj.velocity
    return float(vel[0]), float(vel[1])


class EgoDriver:
    """The driving system under test, fed from the Scenic scene each step."""

    def __init__(self, lay: Layout, profile_json: str | None):
        profile = DriverProfile(**json.loads(profile_json)) if profile_json else DriverProfile()
        m = load_plant_map()
        self.pilot = Autopilot(profile, lay.time_of_day, lay.weather,
                               zone_of={n: p.zone for n, p in m.points.items()})
        self.pilot.drive(LanePath(lay.lane, lay.route), lay.ego_speed_ms)
        self.t = 0.0

    def control(self, ego, objects, dt: float) -> tuple[float, float, float]:
        things = []
        for obj in objects:
            if obj is ego:
                continue
            kind = getattr(obj, "marshalKind", "vehicle")
            vx, vy = _velocity(obj)
            things.append(Thing(obj.marshalId, kind, footprint(obj), vx, vy, tall=kind == "vehicle"))
        pose = Pose(float(ego.position[0]), float(ego.position[1]), plant_yaw(ego.heading), float(ego.speed))
        ctl = self.pilot.step(pose, things, self.t, dt)
        self.t += dt
        steer = max(-1.0, min(1.0, ctl.steer / math.radians(MAX_STEER_DEG)))
        tb = throttle_brake(ctl.accel, ctl.target_speed, pose.speed, ego.metaDriveActor)
        # Scenic steers right for positive values; MetaDrive and the autopilot steer left
        return -steer, max(tb, 0.0), max(-tb, 0.0)

    @property
    def arrived(self) -> bool:
        return self.pilot.path is not None and self.pilot.remaining < 1.2


class LeadCar:
    """A car ahead of ego on the same lane that brakes hard after `brake_at_s`."""

    def __init__(self, lay: Layout, placed: Placed):
        self.pilot = Autopilot(DriverProfile(name="lead"))
        full = LanePath(lay.lane, lay.route)
        rest = [(x, y) for x, y in lay.lane if full.project(x, y) > placed.path_s + 0.5]
        self.pilot.drive(LanePath([(placed.x, placed.y), *rest], [None] * (len(rest) + 1)),
                         placed.speed_ms)
        self.brake_at = placed.brake_at_s or 3.0
        self.t = 0.0

    def control(self, me, dt: float) -> tuple[float, float, float]:
        pose = Pose(float(me.position[0]), float(me.position[1]), plant_yaw(me.heading), float(me.speed))
        ctl = self.pilot.step(pose, [], self.t, dt)
        self.t += dt
        if self.t >= self.brake_at:
            return -max(-1.0, min(1.0, ctl.steer / math.radians(MAX_STEER_DEG))), 0.0, 0.5
        steer = max(-1.0, min(1.0, ctl.steer / math.radians(MAX_STEER_DEG)))
        tb = throttle_brake(ctl.accel, ctl.target_speed, pose.speed, me.metaDriveActor)
        return -steer, max(tb, 0.0), max(-tb, 0.0)


def triggered(me, ego, placed: Placed) -> bool:
    return placed.trigger_m is not None and math.dist(
        (float(me.position[0]), float(me.position[1])),
        (float(ego.position[0]), float(ego.position[1]))) <= placed.trigger_m


def crossed(me, placed: Placed) -> bool:
    """Has a crossing actor reached the far side of the road?"""
    yaw = placed.cross_yaw or 0.0
    along = (float(me.position[0]) - placed.x) * math.cos(yaw) + (float(me.position[1]) - placed.y) * math.sin(yaw)
    return along >= CROSS_TO_LAT - placed.lateral0


class Monitor:
    """The metrics the judge reads, measured on MetaDrive geometry every step."""

    def __init__(self, lay: Layout, clip: str | None = None):
        self.lay = lay
        self.clip = clip
        self.t = 0.0
        self.min_gap_person, self.min_gap_vehicle = 99.0, 99.0
        self.speed_near_person, self.max_speed = 0.0, 0.0
        self.contact, self.reached, self.time_to_goal = False, False, TIMEOUT_S
        self.events: list[str] = []
        self.triggered: set[int] = set()
        self._pins: dict[int, tuple] = {}
        self.camera = self._camera() if clip else None

    def _camera(self) -> tuple[float, float]:
        """Top-down clip camera over the actors (MetaDrive coordinates are plant minus map centre)."""
        from marshal_ai.driving.metadrive_backend import map_centre
        spots = [(a.x, a.y) for a in self.lay.actors] or [self.lay.lane[len(self.lay.lane) // 2]]
        cx, cy = sum(x for x, _ in spots) / len(spots), sum(y for _, y in spots) / len(spots)
        mx, my = map_centre()
        return cx - mx, cy - my

    def step(self, ego, objects, driver: EgoDriver, dt: float, sim=None) -> None:
        self.t += dt
        if sim is not None:
            self._pin_parked(objects, sim)
        speed = float(ego.speed)
        self.max_speed = max(self.max_speed, speed * 3.6)
        mine = footprint(ego)
        for obj in objects:
            if obj is ego:
                continue
            gap = mine.distance(footprint(obj))
            placed = self.lay.actors[obj.marshalIndex]
            if obj.marshalKind == "person":
                self.min_gap_person = min(self.min_gap_person, gap)
                if gap < NEAR_PERSON_GAP_M:
                    self.speed_near_person = max(self.speed_near_person, speed * 3.6)
            elif placed.moves or placed.type == "car":
                self.min_gap_vehicle = min(self.min_gap_vehicle, gap)
            if gap <= 0.05 and not self.contact:
                self.contact = True
                self.events.append(f"t={self.t:.1f}s CONTACT with {placed.type} {placed.index} "
                                   f"at {speed * 3.6:.1f} km/h")
        if driver.arrived and not self.reached:
            self.reached, self.time_to_goal = True, self.t
        if self.clip and sim is not None and int(round(self.t / dt)) % 2 == 0:
            sim.client.render(mode="topdown", window=False, screen_record=True, film_size=(2400, 2400),
                              scaling=8, screen_size=(640, 480),
                              semantic_map=True, draw_contour=True)

    def _pin_parked(self, objects, sim) -> None:
        """Braked Bullet vehicles creep a few cm/s; parked proxies must stay where they were put."""
        for obj in objects:
            i = getattr(obj, "marshalIndex", -1)
            if i < 0:
                continue
            placed = self.lay.actors[i]
            if placed.kind == "person" or placed.moves:
                continue
            actor = obj.metaDriveActor
            if i not in self._pins:
                self._pins[i] = (tuple(actor.position), actor.heading_theta)
            actor.set_position(self._pins[i][0])
            actor.set_heading_theta(self._pins[i][1])
            actor.set_velocity([1.0, 0.0], 0.0)

    def mark_triggered(self, placed: Placed) -> bool:
        if placed.index not in self.triggered:
            self.triggered.add(placed.index)
            self.events.append(f"t={self.t:.1f}s {placed.type} {placed.index} starts moving")
        return True

    @property
    def done(self) -> bool:
        return self.reached

    def finish(self, sim=None, driver: EgoDriver | None = None) -> str:
        if self.clip and sim is not None and getattr(sim.client, "top_down_renderer", None) is not None:
            Path(self.clip).parent.mkdir(parents=True, exist_ok=True)
            sim.client.top_down_renderer.generate_gif(self.clip, duration=200)
        events = sorted((driver.pilot.events if driver else []) + self.events,
                        key=lambda e: float(e.split("s", 1)[0].removeprefix("t=")))
        return json.dumps({
            "metrics": {
                "no_contact": 0.0 if self.contact else 1.0,
                "reached_goal": 1.0 if self.reached else 0.0,
                "min_gap_to_person_m": round(self.min_gap_person, 2),
                "min_gap_to_vehicle_m": round(self.min_gap_vehicle, 2),
                "speed_near_person_kmh": round(self.speed_near_person, 2),
                "max_speed_kmh": round(self.max_speed, 2),
                "time_to_goal_s": round(self.time_to_goal, 1),
            },
            "events": events,
            "triggered": sorted(self.triggered),
            "clip": self.clip,
        })
