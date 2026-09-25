"""The driving system on MetaDrive: cars drive the plant road network with real physics.

Runs on a laptop (`MARSHAL_DRIVING=metadrive`). Mission bookkeeping, holds, routines and
status messages work exactly as in the fake backend; the difference is that cars are
MetaDrive vehicles steered by the `Autopilot`, so they queue behind each other, take real
time on corners and stop at whatever stands in their lane.

A car stopped at an unknown object for a few seconds raises `assist.request` on its own with
the options it can actually carry out:

    wait_for_removal      stay stopped; drive on once the object is gone
    pass_left_3kmh        pass the object on the left at 3 km/h
    reroute_<point>       turn round and take the other road through <point>

Simulation-only helpers place objects and people (`place_object`, `place_person`). `view` opens
a window: "topdown" (the whole plant) or "3d" (a camera chasing one car, see `follow`);
`record="runs/plant.gif"` saves the top-down view as a GIF.
"""

from __future__ import annotations

import itertools
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from marshal_ai.driving.api import emit
from marshal_ai.driving.autopilot import (
    CAR_LENGTH_M, CAR_WIDTH_M, Autopilot, Detour, LanePath, Pose, Thing, box,
)
from marshal_ai.driving.fake import FakeDrivingSystem, _Car
from marshal_ai.driving.messages import AssistChoose, AssistRequest, Command, MissionDone, MissionGo
from marshal_ai.lab.sim import DriverProfile
from marshal_ai.plant import PlantMap
from marshal_ai.plant.roadnet import NET_FILE, off_road

log = logging.getLogger(__name__)

CONTROL_DT = 0.1
QUEUE_SPACING_M = 7.0
BAY_ZONES = {"rework"}          # bays are beside the aisle: cars pull in off the lane
BAY_PULL_IN_M = 5.0
THREE_D_FRAME_EVERY = 2         # 3D view: draw one frame per this many 0.1 s steps
PERSON_RADIUS_M = 0.35
OBJECT_SIZE = {"box": (1.0, 1.0), "barrier": (2.0, 0.3), "cone": (0.5, 0.5)}


def _env_class():
    from panda3d.core import loadPrcFileData

    import metadrive.engine.core.engine_core as engine_core   # sets MetaDrive's Panda3D defaults
    # 3D window fixes for Apple GPUs. MetaDrive asks for 8x multisampling on the window and 16x in its
    # PBR pipeline; both are refused ("Could not open window", then no tonemap buffer), 4x works.
    loadPrcFileData("", "multisamples 4")
    if not getattr(engine_core.init, "_marshal_capped", False):
        pbr_init = engine_core.init

        def capped_init(**kwargs):
            kwargs["msaa_samples"] = min(4, kwargs.get("msaa_samples", 4))
            return pbr_init(**kwargs)

        capped_init._marshal_capped = True
        engine_core.init = capped_init
    from metadrive.envs import BaseEnv
    from metadrive.manager.sumo_map_manager import SumoMapManager
    from metadrive.obs.observation_base import DummyObservation

    class PlantEnv(BaseEnv):
        """A MetaDrive world with the plant road network and no built-in traffic or rewards."""

        def reward_function(self, agent):
            return 0, {}

        def cost_function(self, agent):
            return 0, {}

        def done_function(self, agent):
            return False, {}

        def get_single_observation(self):
            return DummyObservation()

        def setup_engine(self):
            super().setup_engine()
            self.engine.register_manager("map_manager", SumoMapManager(self.config["sumo_map"]))

    return PlantEnv


def map_centre(net_file: Path = NET_FILE) -> tuple[float, float]:
    """MetaDrive centres the SUMO network: metadrive = plant - centre."""
    import sumolib
    xmin, ymin, xmax, ymax = sumolib.net.readNet(str(net_file)).getBoundary()
    return (xmin + xmax) / 2, (ymin + ymax) / 2


# Measured at plant speeds on a 1100 kg DefaultVehicle: acceleration per unit of pedal is
# proportional to the vehicle's own engine / brake force, which MetaDrive varies per vehicle.
ACCEL_PER_ENGINE_FORCE = 3.0 / 812.6
DECEL_PER_BRAKE_FORCE = 25.9 / 142.6


def throttle_brake(accel: float, target: float, speed: float, vehicle=None) -> float:
    """MetaDrive's single pedal (throttle > 0, brake < 0) for the wanted acceleration."""
    if target <= 0.0 and speed < 0.3:
        return -1.0                                  # hold the car still
    engine = vehicle.config["max_engine_force"] if vehicle is not None else 812.6
    brake = vehicle.config["max_brake_force"] if vehicle is not None else 142.6
    tb = accel / (ACCEL_PER_ENGINE_FORCE * engine) if accel >= 0 else accel / (DECEL_PER_BRAKE_FORCE * brake)
    return max(-1.0, min(1.0, tb))


@dataclass
class _Physical:
    vehicle: object
    pilot: Autopilot
    legs: list[list[str]] = field(default_factory=list)     # remaining legs; routine stops end a leg
    leg_end: str | None = None
    raised_for: str | None = None                           # object an assist request was raised for
    pinned: tuple | None = None                             # (position, heading) while standing still


class MetaDriveDrivingSystem(FakeDrivingSystem):
    def __init__(self, plant_map: PlantMap, time_scale: float = 1.0, status_period_s: float = 1.0,
                 profile: DriverProfile | None = None, world=None, render: bool = False,
                 record: str | None = None, net_file: Path = NET_FILE, view: str | None = None):
        super().__init__(plant_map, time_scale, status_period_s)
        self.profile = profile or DriverProfile()
        self.world = world                           # FakeWorld: assist scenes are written here
        self.view = view or ("topdown" if render else None)
        self.render_window, self.record = self.view == "topdown", record
        self.followed: str | None = None
        self.pace: float | None = None               # set to e.g. 2.0 to run at 2x real time (live windows)
        self._wall = 0.0
        self._steps = 0
        self.zone_of = {name: p.zone for name, p in plant_map.points.items()}
        self.centre = map_centre(net_file)
        self.t = 0.0
        self.phys: dict[str, _Physical] = {}
        self.objects: dict[str, tuple[object, Thing]] = {}
        self.people: dict[str, tuple[object, Thing]] = {}
        self._case_ids = itertools.count(1)
        self._mission_order = itertools.count(1)
        self._frames = 0
        env_cls = _env_class()
        self.env = env_cls(dict(
            use_render=self.view == "3d", window_size=(1280, 720), show_interface=False, show_logo=False,
            show_fps=False, log_level=logging.CRITICAL, use_mesh_terrain=self.view == "3d",
            height_scale=0.0001,
            decision_repeat=5, physics_world_step_size=CONTROL_DT / 5,
            vehicle_config=dict(spawn_position_heading=[self._md(-40.0, -30.0), 0.0]),
        ))
        self.env.config["sumo_map"] = str(net_file)
        self.env.reset()
        self.engine = self.env.engine
        # the env needs one agent of its own: a parked camera car, well off the roads
        self.ego = list(self.engine.get_objects().values())[0]
        if self.view == "3d":
            from marshal_ai.driving.plant3d import draw
            draw(self.engine, plant_map, self.centre, self._bay_outlines())

    # ---------- coordinates ----------

    def _md(self, x: float, y: float) -> tuple[float, float]:
        return x - self.centre[0], y - self.centre[1]

    def _plant(self, pos) -> tuple[float, float]:
        return float(pos[0]) + self.centre[0], float(pos[1]) + self.centre[1]

    # ---------- simulation-only helpers ----------

    def spawn(self, vin: str, at: str = "line_end") -> None:
        from metadrive.component.vehicle.vehicle_type import DefaultVehicle
        super().spawn(vin, at)
        x, y, yaw = self._spawn_pose(at)
        if at == "line_end":                          # queue up behind the line end
            waiting = sum(1 for c in self.cars.values() if c.location == "line_end" and c.vin != vin)
            x -= waiting * QUEUE_SPACING_M * math.cos(yaw)
            y -= waiting * QUEUE_SPACING_M * math.sin(yaw)
        vehicle = self.engine.agent_manager.spawn_object(DefaultVehicle, vehicle_config=dict(
            spawn_position_heading=[self._md(x, y), yaw], spawn_velocity=[0.0, 0.0]))
        pilot = Autopilot(self.profile, zone_of=self.zone_of)
        self.phys[vin] = _Physical(vehicle, pilot)
        car = self.cars[vin]
        car.x, car.y = x, y

    async def raise_assist(self, vin: str, case_id: str, reason: str, options: list[str],
                           clip_uri: str | None = None) -> None:
        """Scripted stop-and-ask (tests, demos): the car is brought to rest first."""
        ph = self.phys[vin]
        ph.pinned = None
        self._pin(ph, True)
        self.follow(vin)
        car = self.cars[vin]
        car.x, car.y = self._plant(ph.pinned[0])
        await super().raise_assist(vin, case_id, reason, options, clip_uri)

    def follow(self, vin: str) -> None:
        """Point the 3D chase camera at this car (by default: the car that got the latest mission)."""
        self.followed = vin
        if self.view == "3d" and vin in self.phys:
            self.engine.main_camera.track(self.phys[vin].vehicle)

    def _spawn_pose(self, at: str) -> tuple[float, float, float]:
        """Parked in a slot facing into it; on a road, in the right-hand lane facing onward."""
        p = self.map.points[at]
        if self.zone_of.get(at) in BAY_ZONES:
            bay = self._bay(at)
            return bay.centroid.x, bay.centroid.y, self._bay_yaw(at)
        if off_road(at):
            nb = self.map.points[next(iter(self.map.edges[at]))]
            return p.x, p.y, math.atan2(p.y - nb.y, p.x - nb.x)
        onward = [n for n in self.map.edges[at] if not off_road(n)]
        nb = self.map.points[sorted(onward, key=lambda n: -self.map.points[n].x)[0]] if onward else p
        yaw = math.atan2(nb.y - p.y, nb.x - p.x) if nb is not p else 0.0
        return p.x + math.sin(yaw) * 1.75, p.y - math.cos(yaw) * 1.75, yaw

    def place_object(self, obj_id: str, x: float, y: float, kind: str = "box", yaw: float = 0.0) -> None:
        """An object nobody told the car about (a dropped box, a stray barrier)."""
        from metadrive.component.static_object.traffic_object import TrafficBarrier, TrafficWarning
        cls = TrafficBarrier if kind == "barrier" else TrafficWarning
        md = self.engine.spawn_object(cls, position=self._md(x, y), heading_theta=yaw, static=True)
        length, width = OBJECT_SIZE.get(kind, OBJECT_SIZE["box"])
        self.objects[obj_id] = (md, Thing(obj_id, "object", box(x, y, yaw, length, width)))

    def lane_point(self, a: str, b: str, fraction: float = 0.5,
                   lateral: float = 0.0) -> tuple[float, float, float]:
        """A spot on the right-hand lane from point a to point b; lateral > 0 is left of it."""
        path = LanePath.through(self.map, [a, b])
        x, y, yaw = path.point(path.length * fraction)
        return x - math.sin(yaw) * lateral, y + math.cos(yaw) * lateral, yaw

    def place_object_in_lane(self, obj_id: str, a: str, b: str, fraction: float = 0.5,
                             kind: str = "box") -> tuple[float, float]:
        """Put an object in the right-hand lane of the road from point a to point b."""
        x, y, yaw = self.lane_point(a, b, fraction)
        self.place_object(obj_id, x, y, kind, yaw)
        return x, y

    def remove_object(self, obj_id: str) -> None:
        md, _ = self.objects.pop(obj_id)
        self.engine.clear_objects([md.id], force_destroy=True)

    def place_person(self, person_id: str, x: float, y: float) -> None:
        from metadrive.component.traffic_participants.pedestrian import Pedestrian
        md = self.engine.spawn_object(Pedestrian, position=self._md(x, y), heading_theta=0.0)
        md.set_velocity([1.0, 0.0], 0.0)
        self.people[person_id] = (md, Thing(person_id, "person", _circle(x, y, PERSON_RADIUS_M)))

    def remove_person(self, person_id: str) -> None:
        md, _ = self.people.pop(person_id)
        self.engine.clear_objects([md.id], force_destroy=True)

    # ---------- DrivingSystem ----------

    async def send(self, command: Command) -> None:
        await super().send(command)
        if isinstance(command, MissionGo):
            car, ph = self.cars[command.vin], self.phys[command.vin]
            names = list(car.path)
            if car.routine_queue:                     # standing on the track: routine first
                ph.legs = [names] if names else []
                ph.pilot.stop()
                self.follow(command.vin)
                return
            if command.routine is not None and "test_track" in names:
                i = names.index("test_track")
                legs = [names[:i + 1], names[i + 1:]]
            else:
                legs = [names]
            ph.legs = [leg for leg in legs if leg]
            ph.pilot.priority = next(self._mission_order)
            self.follow(command.vin)
            self._next_leg(car, ph)
        elif isinstance(command, AssistChoose):
            self._carry_out(command)

    def _next_leg(self, car: _Car, ph: _Physical) -> None:
        if not ph.legs:
            ph.pilot.stop()
            ph.leg_end = None
            return
        leg = ph.legs.pop(0)
        ph.leg_end = leg[-1]
        speed = (car.mission.max_speed_kmh if car.mission else 10.0) / 3.6
        path = LanePath.through(self.map, leg, start=(car.x, car.y))
        if self.zone_of.get(leg[-1]) in BAY_ZONES and len(path.pts) >= 2:
            path = self._pull_in(path)
        ph.pilot.drive(path, speed)

    def _bay_path(self, name: str) -> LanePath:
        """The way into a bay along its row (the rework row runs west to east)."""
        p = self.map.points[name]
        west = [n for n in self.map.edges[name] if self.map.points[n].x < p.x and self.zone_of.get(n) in BAY_ZONES]
        start = (self.map.points[west[0]].x, self.map.points[west[0]].y) if west else (p.x - 10.0, p.y)
        return self._pull_in(LanePath([(start[0], start[1] - 1.75), (p.x, p.y - 1.75)], [None, name]))

    def _bay_yaw(self, name: str) -> float:
        (ax, ay), (bx, by) = self._bay_path(name).pts[-3:-1]
        return math.atan2(by - ay, bx - ax)

    def _bay(self, name: str):
        return box(*self._bay_path(name).pts[-1], self._bay_yaw(name), 5.4, 2.8)

    def _bay_outlines(self) -> list:
        """Where cars stand in each rework bay (see `_pull_in`), for the 3D view's paint."""
        return [self._bay(name) for name, zone in self.zone_of.items() if zone in BAY_ZONES]

    @staticmethod
    def _pull_in(path: LanePath) -> LanePath:
        """End the path in the bay beside the lane instead of on it, so the lane stays free."""
        (ax, ay), (bx, by) = path.pts[-2], path.pts[-1]
        length = math.hypot(bx - ax, by - ay) or 1.0
        dx, dy = (bx - ax) / length, (by - ay) / length
        lead = min(7.0, length * 0.8)
        pre = (bx - dx * lead, by - dy * lead)
        bay = (bx + dy * BAY_PULL_IN_M + dx * 1.0, by - dx * BAY_PULL_IN_M + dy * 1.0)
        return LanePath([*path.pts[:-1], pre, bay], [*path.names[:-1], None, path.names[-1]])

    def _carry_out(self, choice: AssistChoose) -> None:
        car, ph = self.cars[choice.vin], self.phys[choice.vin]
        obj_id = ph.raised_for                        # stays set: one request per object
        option = choice.option
        if option.startswith("pass_left") and obj_id in self.objects:
            thing = self.objects[obj_id][1]
            c = thing.shape.centroid
            ph.pilot.detour = Detour(obj_id, ph.pilot.path.project(c.x, c.y))
        elif option.startswith("reroute_"):
            via = option.removeprefix("reroute_")
            final = ph.leg_end
            rest = [p for leg in ph.legs for p in leg]
            start = car.location if car.location in self.map.points else via
            names = self.map.route(start, via) + self.map.route(via, final)[1:]
            ph.legs = [names] + ([rest] if rest else [])
            car.path = names[1:] + rest
            if obj_id:
                ph.pilot.ignore.discard(obj_id)
            self._next_leg(car, ph)
        # wait_for_removal: nothing to do, the car drives on once its lane is clear

    # ---------- the simulation loop ----------

    async def tick(self, dt: float) -> None:
        steps = max(1, round(dt / CONTROL_DT))
        for _ in range(steps):
            await self._control_step()
        self._since_status += dt
        if self._since_status >= self.status_period_s:
            self._since_status = 0.0
            for car in self.cars.values():
                await emit(self._handlers, self._status(car))

    def _things(self, vin: str) -> list[Thing]:
        things = [t for _, t in self.objects.values()] + [t for _, t in self.people.values()]
        for other, ph in self.phys.items():
            if other == vin:
                continue
            v = ph.vehicle
            x, y = self._plant(v.position)
            vx, vy = v.velocity
            car = self.cars[other]
            waiting = ph.pilot.path is not None and car.mission is not None and v.speed < 0.3
            things.append(Thing(other, "vehicle", box(x, y, v.heading_theta, CAR_LENGTH_M, CAR_WIDTH_M),
                                float(vx), float(vy), tall=True, priority=ph.pilot.priority,
                                waiting=waiting))
        return things

    async def _control_step(self) -> None:
        for vin, car in self.cars.items():
            ph = self.phys[vin]
            v = ph.vehicle
            if car.held and car.hold_left_s is not None:
                car.hold_left_s -= CONTROL_DT
                if car.hold_left_s <= 0:
                    car.held, car.hold_left_s = False, None
            if car.routine_queue:
                await self._routine(car, CONTROL_DT)
                if not car.routine_queue:
                    self._next_leg(car, ph)
            active = (car.mission is not None and ph.pilot.path is not None and not car.held
                      and not car.assist_case and not car.routine_queue)
            pose = Pose(car.x, car.y, v.heading_theta, v.speed)
            if active:
                ctl = ph.pilot.step(pose, self._things(vin), self.t, CONTROL_DT)
                steer = max(-1.0, min(1.0, ctl.steer / math.radians(v.max_steering)))
                v.before_step([steer, throttle_brake(ctl.accel, ctl.target_speed, v.speed, v)])
                standing = ctl.target_speed <= 0.0 and v.speed < 0.3
            else:
                v.before_step([0.0, throttle_brake(-3.0, 0.0, v.speed)])
                standing = v.speed < 0.3
            self._pin(ph, standing)
        self._step_world()
        self.t += CONTROL_DT
        self._draw()
        if self.pace:
            behind = CONTROL_DT / self.pace - (time.monotonic() - self._wall)
            if behind > 0:
                time.sleep(behind)
            self._wall = time.monotonic()
        for vin, car in self.cars.items():
            await self._sense(car, self.phys[vin])

    def _step_world(self) -> None:
        """One 0.1 s physics step. In 3D, a frame costs ~80 ms on a laptop GPU, so draw every other step."""
        self._steps += 1
        if self.view != "3d" or self._steps % THREE_D_FRAME_EVERY == 0:
            self.env.step([0.0, -0.3])
            return
        tasks = self.engine.task_manager
        tasks.step = lambda: None                    # skip this step's frame (and window events)
        try:
            self.env.step([0.0, -0.3])
        finally:
            del tasks.step

    @staticmethod
    def _pin(ph: _Physical, standing: bool) -> None:
        """Braked Bullet vehicles still creep a few cm/s; a stopped car stays where it stopped."""
        v = ph.vehicle
        if not standing:
            ph.pinned = None
            return
        if ph.pinned is None:
            ph.pinned = (tuple(v.position), v.heading_theta)
        v.set_position(ph.pinned[0])
        v.set_heading_theta(ph.pinned[1])
        v.set_velocity([1.0, 0.0], 0.0)

    async def _sense(self, car: _Car, ph: _Physical) -> None:
        v = ph.vehicle
        car.x, car.y = self._plant(ph.pinned[0] if ph.pinned else v.position)
        car.speed_kmh = 0.0 if ph.pinned else v.speed * 3.6
        pilot = ph.pilot
        if car.mission is None or pilot.path is None:
            return
        # passed points: the car's location is the last named point behind it
        path = pilot.path
        for cum, name in zip(path.cum, path.names):
            if name is not None and cum <= pilot.s + 1.0 and car.path and car.path[0] == name:
                car.path.pop(0)
                car.location = name
        car.travelled = pilot.s
        car.total = path.length
        pose = Pose(car.x, car.y, v.heading_theta, v.speed)
        if pilot.blocked_by and not car.assist_case and ph.raised_for != pilot.blocked_by:
            await self._raise(car, ph, pilot.blocked_by)
        if not pilot.arrived(pose):
            return
        car.location = ph.leg_end or car.location
        if ph.legs:                                   # a routine stop on the way
            if not self._starts_routine(car):
                self._next_leg(car, ph)
            else:
                pilot.stop()
            return
        pilot.stop()
        car.path = []
        await self._finish(car)

    async def _finish(self, car: _Car) -> None:
        mission = car.mission
        car.mission, car.speed_kmh = None, 0.0
        await emit(self._handlers, self._status(car, mission_id=mission.mission_id, progress=1.0))
        await emit(self._handlers, MissionDone(mission_id=mission.mission_id, vin=car.vin,
                                               location=car.location))

    async def _raise(self, car: _Car, ph: _Physical, obj_id: str) -> None:
        case_id = f"AC-{next(self._case_ids)}"
        ph.raised_for = obj_id
        options = ["wait_for_removal", "pass_left_3kmh"]
        via = self._alternative(car, ph)
        if via:
            options.append(f"reroute_{via}")
        car.assist_case = case_id
        if self.world is not None and case_id not in self.world.scenes:
            self.world.scenes[case_id] = self._scene(car, obj_id)
        await emit(self._handlers, AssistRequest(
            case_id=case_id, vin=car.vin, location=car.location, reason="unknown object in lane",
            options=options, clip_uri=f"metadrive://{case_id}"))

    def _alternative(self, car: _Car, ph: _Physical) -> str | None:
        """A point on another road to the same place, avoiding the blocked segment."""
        if not car.path or not ph.leg_end:
            return None
        a, b = car.location, car.path[0]
        m = self.map
        saved = (m.edges[a].pop(b, None), m.edges[b].pop(a, None))
        try:
            detour = m.route(a, ph.leg_end)
        except (ValueError, KeyError):
            return None
        finally:
            if saved[0] is not None:
                m.edges[a][b] = saved[0]
            if saved[1] is not None:
                m.edges[b][a] = saved[1]
        fresh = [p for p in detour[1:-1] if p not in car.path and not off_road(p)]
        return fresh[0] if fresh else None

    def _scene(self, car: _Car, obj_id: str) -> dict:
        """What the clip shows, measured in the simulator (ground truth for offline perception)."""
        shape = self.objects[obj_id][1].shape
        here = _circle(car.x, car.y, 0.1)
        return {"object": "cardboard box", "where": f"in lane after {car.location}",
                "distance_m": round(here.distance(shape) - CAR_LENGTH_M / 2, 1),
                "people": [{"role": "worker", "distance_m": round(here.distance(t.shape), 1)}
                           for _, t in self.people.values() if here.distance(t.shape) < 15]}

    # ---------- rendering ----------

    def _draw(self) -> None:
        if not (self.render_window or self.record):
            return                                    # the 3D window draws itself on every env.step
        self._frames += 1
        if self._frames % 2:
            return
        # the whole plant: 240 m x 80 m at 5 px/m (the film must hold the full map at that scale)
        self.env.render(mode="topdown", window=self.render_window, screen_record=bool(self.record),
                        film_size=(3000, 3000), scaling=5, screen_size=(1300, 500),
                        camera_position=(0, 0), semantic_map=True, draw_contour=True)

    async def stop(self) -> None:
        await super().stop()
        self.close()

    def close(self) -> None:
        if self.env is None:
            return
        if self.record and self._frames:
            Path(self.record).parent.mkdir(parents=True, exist_ok=True)
            self.env.top_down_renderer.generate_gif(self.record, duration=int(CONTROL_DT * 2000))
        self.env.close()
        self.env = None


def _circle(x: float, y: float, r: float):
    from shapely.geometry import Point
    return Point(x, y).buffer(r)
