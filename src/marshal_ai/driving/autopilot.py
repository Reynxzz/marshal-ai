"""The stand-in driving system for MetaDrive runs (plant backend and test lab).

Marshal never drives. On a laptop something still has to, so this is the driving system under
test: it follows a lane path at the mission speed and perceives only what its `DriverProfile`
allows. The profile sets detection range and reaction time by light and weather, and line of
sight is cut by what stands in the way. It brakes for anything in or entering its lane, slows
near people and stops at unknown objects. A car stopped at an object for `ASSIST_AFTER_S`
reports itself blocked, so the plant backend can raise an assist request.

The plant backend and the Scenic scenarios use the same class, so a release is judged on the
behaviour the plant runs. Geometry is in plant metres; yaw is radians from +x, counter-clockwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import substring

from marshal_ai.lab.sim import DriverProfile
from marshal_ai.plant import PlantMap

LANE_OFFSET_M = 1.75            # lane centre is this far right of the road centre line
WHEELBASE_M = 2.6
CAR_LENGTH_M, CAR_WIDTH_M = 4.5, 1.85
CORRIDOR_MARGIN_M = 0.5         # beyond the car's half width
ASSIST_AFTER_S = 3.0
STOP_GAP_M = 1.0


def box(x: float, y: float, yaw: float, length: float, width: float) -> Polygon:
    c, s = math.cos(yaw), math.sin(yaw)
    hl, hw = length / 2, width / 2
    return Polygon([(x + c * dx - s * dy, y + s * dx + c * dy)
                    for dx, dy in ((hl, hw), (-hl, hw), (-hl, -hw), (hl, -hw))])


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class LanePath:
    """A polyline the car drives along, with plant point names at its vertices."""

    def __init__(self, pts: list[tuple[float, float]], names: list[str | None]):
        clean_pts, clean_names = [pts[0]], [names[0]]
        for p, n in zip(pts[1:], names[1:]):
            if math.dist(p, clean_pts[-1]) > 1e-3:
                clean_pts.append(p)
                clean_names.append(n)
        if len(clean_pts) == 1:
            clean_pts.append((clean_pts[0][0] + 1e-3, clean_pts[0][1]))
            clean_names.append(clean_names[0])
        self.pts, self.names = clean_pts, clean_names
        self.line = LineString(self.pts)
        self.length = self.line.length
        self.cum = [0.0]
        for a, b in zip(self.pts, self.pts[1:]):
            self.cum.append(self.cum[-1] + math.dist(a, b))

    @classmethod
    def through(cls, plant_map: PlantMap, names: list[str], offset: float = LANE_OFFSET_M,
                start: tuple[float, float] | None = None) -> "LanePath":
        """The right-hand lane through named points; `start` prefixes the car's own position."""
        raw = [(plant_map.points[n].x, plant_map.points[n].y) for n in names]
        pts = _offset(raw, -offset)
        labels: list[str | None] = list(names)
        if start is not None:
            pts, labels = [start, *pts], [None, *labels]
        return cls(pts, labels)

    def project(self, x: float, y: float) -> float:
        return self.line.project(Point(x, y))

    def point(self, s: float) -> tuple[float, float, float]:
        s = min(max(s, 0.0), self.length)
        p = self.line.interpolate(s)
        q = self.line.interpolate(min(s + 0.5, self.length))
        r = self.line.interpolate(max(s - 0.5, 0.0))
        return p.x, p.y, math.atan2(q.y - r.y, q.x - r.x)

    def lateral(self, x: float, y: float, s: float) -> float:
        """Signed distance from the path at s, left positive."""
        px, py, yaw = self.point(s)
        return -math.sin(yaw) * (x - px) + math.cos(yaw) * (y - py)

    def ahead(self, s0: float, s1: float) -> LineString:
        s0, s1 = max(0.0, s0), min(self.length, s1)
        if s1 - s0 < 0.05:
            x, y, yaw = self.point(s0)
            return LineString([(x, y), (x + 0.05 * math.cos(yaw), y + 0.05 * math.sin(yaw))])
        return substring(self.line, s0, s1)

    def name_near(self, s: float) -> str | None:
        named = [(abs(c - s), n) for c, n in zip(self.cum, self.names) if n is not None]
        return min(named)[1] if named else None


def _offset(pts: list[tuple[float, float]], d: float) -> list[tuple[float, float]]:
    """Offset a polyline sideways by d (left positive), mitring the corners."""
    if len(pts) < 2:
        return list(pts)
    normals = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        L = math.hypot(x2 - x1, y2 - y1) or 1.0
        normals.append((-(y2 - y1) / L, (x2 - x1) / L))
    out = []
    for i, (x, y) in enumerate(pts):
        if i == 0:
            nx, ny = normals[0]
        elif i == len(pts) - 1:
            nx, ny = normals[-1]
        else:
            ax, ay = normals[i - 1]
            bx, by = normals[i]
            mx, my = ax + bx, ay + by
            m = math.hypot(mx, my)
            if m < 1e-6:                        # a U-turn: keep the incoming side
                nx, ny = ax, ay
            else:
                mx, my = mx / m, my / m
                scale = min(2.0, 1.0 / max(0.5, mx * ax + my * ay))
                nx, ny = mx * scale, my * scale
        out.append((x + d * nx, y + d * ny))
    return out


@dataclass
class Thing:
    """Something in the world the car may perceive."""

    id: str
    kind: str                       # "person" | "vehicle" | "object"
    shape: Polygon
    vx: float = 0.0
    vy: float = 0.0
    tall: bool = False              # blocks line of sight (forklift, pallet stack, parked car, truck)
    priority: int | None = None     # vehicles: lower goes first when two wait for each other
    waiting: bool = False           # vehicles: stopped but wants to move

    @property
    def moving(self) -> bool:
        return math.hypot(self.vx, self.vy) > 0.2


@dataclass
class Pose:
    x: float
    y: float
    yaw: float
    speed: float                    # m/s


@dataclass
class Control:
    steer: float                    # front wheel angle, radians, left positive
    accel: float                    # m/s^2 wanted, negative brakes
    target_speed: float


@dataclass
class _Memory:
    seen_at: float | None = None
    react_at: float | None = None
    was_hidden_by_tall: bool = False


@dataclass
class Detour:
    """Pass one object on the left at low speed (the "pass_left" assist option)."""

    thing_id: str
    s: float
    offset_m: float = 3.2
    speed_ms: float = 3 / 3.6
    ramp_m: float = 7.0

    def lateral(self, s: float) -> float:
        d = abs(s - self.s)
        if d <= 3.0:
            return self.offset_m
        if d >= 3.0 + self.ramp_m:
            return 0.0
        return self.offset_m * (1 - (d - 3.0) / self.ramp_m)


@dataclass
class Autopilot:
    profile: DriverProfile = field(default_factory=DriverProfile)
    time_of_day: str = "noon"
    weather: str = "clear"
    zone_of: dict[str, str] = field(default_factory=dict)       # plant point -> zone, for speed caps
    path: LanePath | None = None
    speed_ms: float = 0.0
    s: float = 0.0
    detour: Detour | None = None
    ignore: set[str] = field(default_factory=set)
    priority: int = 0
    blocked_by: str | None = None
    events: list[str] = field(default_factory=list)
    _memory: dict[str, _Memory] = field(default_factory=dict)
    _blocked_since: float | None = None
    _braking_for: set[str] = field(default_factory=set)

    def drive(self, path: LanePath, speed_ms: float) -> None:
        self.path, self.speed_ms, self.s = path, speed_ms, 0.0
        self.detour, self.blocked_by, self._blocked_since = None, None, None

    def stop(self) -> None:
        self.path = None

    @property
    def remaining(self) -> float:
        return self.path.length - self.s if self.path else 0.0

    def arrived(self, pose: Pose) -> bool:
        return self.path is not None and self.remaining < 1.2 and pose.speed < 0.5

    # ---------- perception ----------

    def _range(self, thing: Thing) -> float:
        rng = self.profile.detect_range_m[self.time_of_day] * self.profile.weather_range[self.weather]
        if thing.kind == "person":
            return rng
        return max(rng, 30.0 * self.profile.weather_range[self.weather])

    def _perceive(self, pose: Pose, things: list[Thing], t: float) -> list[Thing]:
        """Things the car has seen and had time to react to."""
        eye = Point(pose.x + math.cos(pose.yaw) * CAR_LENGTH_M / 2,
                    pose.y + math.sin(pose.yaw) * CAR_LENGTH_M / 2)
        blockers = [th for th in things if th.tall]
        reacted = []
        for th in things:
            mem = self._memory.setdefault(th.id, _Memory())
            if mem.seen_at is None:
                target = th.shape.centroid
                dist = eye.distance(th.shape)
                bearing = wrap(math.atan2(target.y - eye.y, target.x - eye.x) - pose.yaw)
                if dist > self._range(th) or abs(bearing) > math.radians(100):
                    continue
                sight = LineString([eye, target])
                hidden_by = [b for b in blockers if b.id != th.id and sight.crosses(b.shape)]
                if hidden_by:
                    mem.was_hidden_by_tall = True
                    continue
                latency = self.profile.latency_s[self.time_of_day]
                if mem.was_hidden_by_tall:
                    latency += self.profile.tall_occluder_latency_s
                mem.seen_at, mem.react_at = t, t + latency
                self.events.append(f"t={t:.1f}s detects {th.kind} {th.id} at {dist:.1f} m")
            if mem.react_at is not None and t >= mem.react_at:
                reacted.append(th)
        return reacted

    # ---------- planning and control ----------

    def step(self, pose: Pose, things: list[Thing], t: float, dt: float) -> Control:
        if self.path is None:
            return Control(0.0, -self.profile.decel_ms2[self.weather], 0.0)
        s_near = self.path.project(pose.x, pose.y)
        self.s = max(self.s, s_near) if abs(s_near - self.s) < 8 else s_near
        v = pose.speed
        decel = self.profile.decel_ms2[self.weather]

        cap = self.speed_ms
        name = self.path.name_near(self.s)
        zone = self.zone_of.get(name) if name else None
        zone_cap = self.profile.zone_speed_caps_kmh.get(f"{zone}@{self.time_of_day}") if zone else None
        if zone_cap is not None:
            cap = min(cap, zone_cap / 3.6)
        if self.detour and abs(self.s - self.detour.s) < 3.0 + self.detour.ramp_m:
            cap = min(cap, self.detour.speed_ms)

        # stop smoothly at the end of the path
        cap = min(cap, math.sqrt(max(0.0, 2 * 1.0 * (self.remaining - 0.3))))

        stop_dist = v * v / (2 * decel)
        look = stop_dist + STOP_GAP_M + CAR_LENGTH_M / 2 + 4.0
        lane = self.path.ahead(self.s, self.s + look)
        if self.detour is not None:
            lane = LineString([self._target(s) for s in _frange(self.s, min(self.path.length, self.s + look), 0.5)]
                              or [self._target(self.s), self._target(self.s + 0.1)])
        corridor = lane.buffer(CAR_WIDTH_M / 2 + CORRIDOR_MARGIN_M, cap_style=2)

        hazard: Thing | None = None
        for th in self._perceive(pose, things, t):
            if th.id in self.ignore or (self.detour and th.id == self.detour.thing_id):
                continue
            ahead = self.path.project(th.shape.centroid.x, th.shape.centroid.y) > self.s - 1.0
            if not ahead:
                continue
            if (th.kind == "vehicle" and th.waiting and th.priority is not None
                    and th.priority > self.priority and _front(pose).distance(th.shape) > 1.5):
                continue                             # it yields to us
            in_lane = corridor.intersects(th.shape)
            entering = th.moving and corridor.intersects(
                _translate(th.shape, th.vx * 1.5, th.vy * 1.5).union(th.shape).convex_hull)
            if th.kind == "vehicle" and th.moving and in_lane:
                # follow a moving vehicle; brake only if it is slower and close
                gap = Point(pose.x, pose.y).distance(th.shape) - CAR_LENGTH_M / 2
                closing = v - math.hypot(th.vx, th.vy)
                if gap < stop_dist + STOP_GAP_M + 1.0 and closing > -0.1:
                    hazard = hazard or th
            elif in_lane or entering:
                hazard = hazard or th
            if th.kind == "person" and Point(pose.x, pose.y).distance(th.shape) <= self.profile.near_person_distance_m:
                cap = min(cap, self.profile.near_person_speed_kmh / 3.6)

        if hazard is not None:
            accel = -decel
            if hazard.id not in self._braking_for:
                self._braking_for.add(hazard.id)
                self.events.append(f"t={t:.1f}s brakes for {hazard.kind} {hazard.id}")
            if hazard.kind == "object" and not hazard.moving and v < 0.3:
                self._blocked_since = self._blocked_since if self._blocked_since is not None else t
                if t - self._blocked_since >= ASSIST_AFTER_S:
                    self.blocked_by = hazard.id
            target = 0.0
        else:
            self._blocked_since, self.blocked_by = None, None
            target = cap
            if v > cap:
                accel = max(-2.0, (cap - v) / max(dt, 0.1))
            else:
                accel = min(self.profile.accel_ms2, (cap - v) / max(dt, 0.1))

        # pure pursuit on the (possibly shifted) path
        ld = max(3.5, 1.2 * v + 2.5)
        tx, ty = self._target(self.s + ld)
        alpha = wrap(math.atan2(ty - pose.y, tx - pose.x) - pose.yaw)
        if abs(alpha) > math.radians(100):          # target behind: turn on full lock, slowly
            steer = math.copysign(math.radians(40), alpha)
            target = min(target, 1.0)
            accel = min(accel, (1.0 - v) / max(dt, 0.1))
        else:
            steer = math.atan2(2 * WHEELBASE_M * math.sin(alpha), ld)
        return Control(steer, accel, target)

    def _target(self, s: float) -> tuple[float, float]:
        x, y, yaw = self.path.point(s)
        d = self.detour.lateral(s) if self.detour else 0.0
        return x - math.sin(yaw) * d, y + math.cos(yaw) * d


def _front(pose: Pose) -> Point:
    return Point(pose.x + math.cos(pose.yaw) * CAR_LENGTH_M / 2,
                 pose.y + math.sin(pose.yaw) * CAR_LENGTH_M / 2)


def _translate(poly: Polygon, dx: float, dy: float) -> Polygon:
    from shapely.affinity import translate
    return translate(poly, dx, dy)


def _frange(a: float, b: float, step: float) -> list[float]:
    n = int((b - a) / step)
    return [a + i * step for i in range(n + 1)] if n >= 1 else []
