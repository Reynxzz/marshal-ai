"""A small deterministic simulator for scenario specs.

It runs the test lab (flows 6 and 7) locally, without CARLA. The ego car follows its
route under a `DriverProfile`, a simplified model of the driving system under test:
detection range and reaction time depend on light, weather and occluders, it stops for
people crossing its path and slows near people it can see. Same spec, same result.

On the VM the CARLA runner replaces this with the compiled scenario and BehaviorAgent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from marshal_ai.lab.spec import ActorType, ScenarioSpec
from marshal_ai.plant import PlantMap

DT = 0.05
TIMEOUT_S = 120.0
CAR_HALF_LEN = 2.4
CAR_HALF_WID = 1.0
RADIUS = {ActorType.WORKER: 0.3, ActorType.FORKLIFT: 1.0, ActorType.CAR: 1.0,
          ActorType.PARKED_CAR: 1.0, ActorType.PALLET_STACK: 0.6}
TALL = {ActorType.FORKLIFT, ActorType.PALLET_STACK}
HIDE_LATERAL = {ActorType.FORKLIFT: 1.8, ActorType.PALLET_STACK: 1.8, ActorType.PARKED_CAR: 2.2}
ROADSIDE_LAT = 3.5          # parked objects stand beside the lane
WORKER_START_LAT = 3.0
CROSSED_LAT = -5.0          # a crossing actor walks this far to the other side
NEAR_PERSON_GAP_M = 2.0     # "near a person" for the speed metric


@dataclass(frozen=True)
class DriverProfile:
    """The driving system under test. A new software release is a new profile."""

    name: str = "behavior-agent-1.0"
    detect_range_m: dict = field(default_factory=lambda: {"noon": 25.0, "dusk": 18.0, "night": 10.0})
    weather_range: dict = field(default_factory=lambda: {"clear": 1.0, "rain": 0.85, "fog": 0.6})
    latency_s: dict = field(default_factory=lambda: {"noon": 0.3, "dusk": 0.4, "night": 0.8})
    tall_occluder_latency_s: float = 0.3
    decel_ms2: dict = field(default_factory=lambda: {"clear": 4.0, "rain": 3.2, "fog": 4.0})
    accel_ms2: float = 1.0
    near_person_speed_kmh: float = 4.5
    near_person_distance_m: float = 10.0
    zone_speed_caps_kmh: dict = field(default_factory=dict)   # "zone@time_of_day" -> km/h


@dataclass
class _Mover:
    index: int
    type: ActorType
    s: float
    lat: float
    radius: float
    person: bool
    static: bool = True
    speed: float = 0.0                  # m/s while moving
    v_s: float = 0.0
    v_lat: float = 0.0
    trigger_m: float | None = None
    triggered: bool = False
    occluder: ActorType | None = None
    brake_at_s: float | None = None     # car ahead: time it brakes
    seen_at: float | None = None
    react_at: float | None = None


@dataclass
class SimResult:
    metrics: dict[str, float]
    events: list[str]
    triggered: list[int]                # indices of actors whose trigger fired
    duration_s: float


class _Path:
    def __init__(self, pts: list[tuple[float, float]], names: list[str]):
        self.pts, self.names = pts, names
        self.cum = [0.0]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            self.cum.append(self.cum[-1] + math.hypot(x2 - x1, y2 - y1))
        self.length = self.cum[-1]

    def project(self, x: float, y: float) -> float:
        best, best_s = math.inf, 0.0
        for i, ((x1, y1), (x2, y2)) in enumerate(zip(self.pts, self.pts[1:])):
            dx, dy = x2 - x1, y2 - y1
            seg = dx * dx + dy * dy
            t = 0.0 if seg == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / seg))
            px, py = x1 + t * dx, y1 + t * dy
            d = math.hypot(x - px, y - py)
            if d < best:
                best, best_s = d, self.cum[i] + t * math.sqrt(seg)
        return best_s

    def nearest_point(self, s: float) -> str:
        i = min(range(len(self.cum)), key=lambda k: abs(self.cum[k] - s))
        return self.names[i]


def _build_path(spec: ScenarioSpec, m: PlantMap) -> _Path:
    names: list[str] = [spec.ego.waypoints[0]]
    for a, b in zip(spec.ego.waypoints, spec.ego.waypoints[1:]):
        names += m.route(a, b)[1:]
    return _Path([(m.points[n].x, m.points[n].y) for n in names], names)


def _box_gap(ego_s: float, mv: _Mover, ego_half_len: float = CAR_HALF_LEN) -> float:
    other_half = CAR_HALF_LEN if mv.type == ActorType.CAR else 0.0
    dx = max(0.0, abs(mv.s - ego_s) - ego_half_len - other_half)
    dy = max(0.0, abs(mv.lat) - CAR_HALF_WID)
    return max(0.0, math.hypot(dx, dy) - mv.radius)


def simulate(spec: ScenarioSpec, plant_map: PlantMap, driver: DriverProfile = DriverProfile()) -> SimResult:
    path = _build_path(spec, plant_map)
    tod, weather = spec.time_of_day.value, spec.weather.value
    v_target = spec.ego.speed_kmh / 3.6
    events: list[str] = []

    # place actors
    movers: list[_Mover] = []
    occluder_s: dict[ActorType, float] = {}
    for i, a in enumerate(spec.actors):
        if a.position and a.type in HIDE_LATERAL:
            p = plant_map.points[a.position]
            occluder_s[a.type] = path.project(p.x, p.y)
    for i, a in enumerate(spec.actors):
        radius, person = RADIUS[a.type], a.type == ActorType.WORKER
        trigger = a.trigger.ego_distance_m if a.trigger else None
        if a.start and a.start.startswith("behind:"):
            occ = ActorType(a.start.removeprefix("behind:"))
            s0 = occluder_s.get(occ, 0.0) + 1.5
            mv = _Mover(i, a.type, s0, WORKER_START_LAT, radius, person, occluder=occ)
        else:
            point = plant_map.points[a.position or a.start]
            s0 = path.project(point.x, point.y)
            lat0 = ROADSIDE_LAT if a.type != ActorType.WORKER else WORKER_START_LAT
            mv = _Mover(i, a.type, s0, lat0, radius, person)
        moves = a.state in ("moving", "crossing") or (person and (trigger is not None or mv.occluder))
        if a.type == ActorType.CAR and a.state == "braking":
            gap0 = trigger if trigger is not None else 10.0
            mv = _Mover(i, a.type, gap0 + 2 * CAR_HALF_LEN, 0.0, radius, False, static=False,
                        v_s=v_target, brake_at_s=3.0)
            mv.triggered = True
        elif moves:
            mv.static = False
            mv.speed = a.walk_speed_ms if person else (a.speed_kmh or 5.0) / 3.6
            mv.trigger_m = trigger if trigger is not None else 8.0
        movers.append(mv)

    ego_s, ego_v, t = 0.0, 0.0, 0.0
    braking_for: set[int] = set()
    min_gap_person, min_gap_vehicle = 99.0, 99.0
    speed_near_person, max_speed = 0.0, 0.0
    contact, reached, time_to_goal = False, False, TIMEOUT_S
    rng = driver.detect_range_m[tod] * driver.weather_range[weather]
    decel = driver.decel_ms2[weather]

    while t < TIMEOUT_S:
        # actors
        for mv in movers:
            if mv.brake_at_s is not None:
                if t >= mv.brake_at_s:
                    mv.v_s = max(0.0, mv.v_s - 4.0 * DT)
                mv.s += mv.v_s * DT
                continue
            if mv.static:
                continue
            if not mv.triggered and mv.s - ego_s <= mv.trigger_m:
                mv.triggered = True
                mv.v_lat = -mv.speed
                events.append(f"t={t:.1f}s {mv.type.value} {mv.index} starts moving, ego {mv.s - ego_s:.1f} m away")
            if mv.triggered and mv.lat > CROSSED_LAT:
                mv.lat = max(CROSSED_LAT, mv.lat + mv.v_lat * DT)

        # perception and reaction
        hazard = False
        cap = v_target
        zone = plant_map.points[path.nearest_point(ego_s)].zone
        zone_cap = driver.zone_speed_caps_kmh.get(f"{zone}@{tod}")
        if zone_cap is not None:
            cap = min(cap, zone_cap / 3.6)
        for mv in movers:
            ds = mv.s - ego_s
            hidden = mv.occluder is not None and mv.lat > HIDE_LATERAL[mv.occluder]
            dist = math.hypot(ds, mv.lat)
            in_range = dist <= (max(rng, 30.0 * driver.weather_range[weather]) if not mv.person else rng)
            if mv.seen_at is None and not hidden and in_range and ds > -CAR_HALF_LEN:
                mv.seen_at = t
                latency = driver.latency_s[tod]
                if mv.occluder in TALL:
                    latency += driver.tall_occluder_latency_s
                mv.react_at = t + latency
                events.append(f"t={t:.1f}s ego detects {mv.type.value} {mv.index} at {dist:.1f} m")
            if mv.react_at is None or t < mv.react_at or ds < -CAR_HALF_LEN:
                continue
            corridor = CAR_HALF_WID + mv.radius + 0.5
            if mv.type == ActorType.CAR and abs(mv.lat) < corridor:
                stop_dist = ego_v ** 2 / (2 * decel)
                if ds - 2 * CAR_HALF_LEN < stop_dist + 2.0 and mv.v_s < ego_v + 0.1:
                    hazard = True
            elif not mv.static and ds > 0 and (abs(mv.lat) < corridor or (mv.v_lat < 0 and mv.lat > 0)):
                hazard = True
            elif mv.person and ds > 0 and dist <= driver.near_person_distance_m:
                cap = min(cap, driver.near_person_speed_kmh / 3.6)
            if hazard and mv.index not in braking_for:
                braking_for.add(mv.index)
                events.append(f"t={t:.1f}s ego brakes for {mv.type.value} {mv.index}")

        # ego dynamics
        if hazard:
            ego_v = max(0.0, ego_v - decel * DT)
        elif ego_v > cap:
            ego_v = max(cap, ego_v - 2.0 * DT)
        else:
            ego_v = min(cap, ego_v + driver.accel_ms2 * DT)
        ego_s += ego_v * DT
        t += DT
        max_speed = max(max_speed, ego_v * 3.6)

        # metrics
        for mv in movers:
            gap = _box_gap(ego_s, mv)
            if mv.person:
                min_gap_person = min(min_gap_person, gap)
                if gap < NEAR_PERSON_GAP_M:
                    speed_near_person = max(speed_near_person, ego_v * 3.6)
            elif not mv.static or mv.type == ActorType.CAR:
                min_gap_vehicle = min(min_gap_vehicle, gap)
            if gap <= 0.0 and not contact:
                contact = True
                events.append(f"t={t:.1f}s CONTACT with {mv.type.value} {mv.index} at {ego_v * 3.6:.1f} km/h")
        if ego_s >= path.length - 0.5:
            reached, time_to_goal = True, t
            break

    metrics = {
        "no_contact": 0.0 if contact else 1.0,
        "reached_goal": 1.0 if reached else 0.0,
        "min_gap_to_person_m": round(min_gap_person, 2),
        "min_gap_to_vehicle_m": round(min_gap_vehicle, 2),
        "speed_near_person_kmh": round(speed_near_person, 2),
        "max_speed_kmh": round(max_speed, 2),
        "time_to_goal_s": round(time_to_goal, 1),
    }
    return SimResult(metrics, events, [m.index for m in movers if m.triggered], round(t, 2))
