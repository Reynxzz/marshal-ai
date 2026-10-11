#!/usr/bin/env python3
"""Dress Town05's plant road as a car plant: props and parked vehicles, none of them on a plant lane.

Town05 is a city map. The plant runs along road 34 (three driving lanes, then the shoulder where the
rework bays and yard slots are, a 0.6 m strip and a sidewalk, forest beyond); across the median, the
other carriageway is unused by the plant and lined with houses. This places, from the map's own
lanes measured at run time:

  - cones along the outer edge of the shoulder at the inspection gate and the test track, where no
    car parks (the bays and yard slots on the shoulder start further on)
  - at the line end, stacked boxes and barrels; at the gate, calibration boards and a warning board
  - beside every rework bay, on the sidewalk: barrels, boxes, an iron plank and a work sign
  - on the unused carriageway: semi-trailer trucks and rows of finished cars along the straight
    (hiding the houses, and leaving the plant's own lanes the only open road) and a lot of finished
    cars in the plant's colours at the curve

then checks that nothing stands on a lane the plant's cars drive or park on, and exits 1 if anything
does. Everything is tagged role_name=marshal-dressing where the blueprint allows, and the dressing
of an earlier run is removed first. Run after the map is loaded and before recording starts, so the
filmed replays include it (scripts/carla_recording.sh calls it; MARSHAL_DRESS=0 skips it).

    python3 scripts/dress_plant.py            # dress
    python3 scripts/dress_plant.py --clear    # remove the dressing
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import carla

TAG = "marshal-dressing"
LINE_END = carla.Location(207.27, -13.93, 0.3)
LOADING_BAY = carla.Location(172.49, -180.50, 0.5)
BAYS = {"rework.paint": (211.67, -116.81), "rework.body": (212.89, -106.80),
        "rework.electrical": (213.34, -88.01), "rework.mechanical": (213.24, -97.01),
        "production_control": (209.56, -126.68)}
GATE_Y, TRACK_Y = (-26.0, -42.0), (-42.0, -84.0)       # along road 34, heading -y
FREE_SHOULDER_FROM_Y = -84.0     # the shoulder is free of bays and slots north of this (y greater)
PLANT_COLOURS = ["0,80,200", "230,230,230", "120,120,120", "20,20,20", "200,20,20"]
PROP_TYPES = ("static.prop.dirtdebris01", "static.prop.dirtdebris02", "static.prop.dirtdebris03",
              "static.prop.brokentile01", "static.prop.brokentile02", "static.prop.brokentile03",
              "static.prop.chainbarrier", "static.prop.trafficcone01", "static.prop.box01", "static.prop.box02",
              "static.prop.box03", "static.prop.barrel", "static.prop.ironplank", "static.prop.warningconstruction",
              "static.prop.calibrator", "static.prop.trafficwarning", "static.prop.streetbarrier")


# On the layered map (Town05_Opt, the same roads), the city's own scenery is in layers that can be
# taken away: the houses and the skyline, the highway's lamp posts, its signs and street furniture,
# parked cars. What is left is the roads, the ground and the trees, and the dressing becomes the only
# built thing in view. MARSHAL_PLANT_UNLOAD overrides the list. Tried on 2026-10-10: CARLA 0.9.16 at
# Epic crashed (signal 11) three times within a minute of layers being unloaded, even with only
# Buildings and StreetLights, so this is kept for a later CARLA version and Town05 stays the default.
CITY_LAYERS = ("Buildings", "StreetLights", "ParkedVehicles", "Props", "Decals")


def strip_city(world: carla.World) -> list[str]:
    import os
    names = [n.strip() for n in os.environ.get("MARSHAL_PLANT_UNLOAD", ",".join(CITY_LAYERS)).split(",") if n.strip()]
    for n in names:
        world.unload_map_layer(getattr(carla.MapLayer, n))
    return names


def emit(obj):
    print(json.dumps(obj), flush=True)


def lane(wp, lane_id):
    """The waypoint beside wp on the same road with this lane id, walking right (more negative)."""
    cur = wp
    for _ in range(10):
        if cur is None:
            return None
        if cur.lane_id == lane_id:
            return cur
        cur = cur.get_right_lane()
    return None


def off(tf: carla.Transform, right: float = 0.0, ahead: float = 0.0, up: float = 0.0) -> carla.Location:
    f, r = tf.get_forward_vector(), tf.get_right_vector()
    loc = tf.location + r * right + f * ahead
    loc.z += up
    return loc


def route_samples(m: carla.Map, step: float = 4.0) -> list[carla.Waypoint]:
    """Lane -2 of the plant road from the line end to the loading bay, every `step` metres."""
    wp = m.get_waypoint(LINE_END, lane_type=carla.LaneType.Driving)
    out = [wp]
    closest = wp.transform.location.distance(LOADING_BAY)
    for _ in range(80):
        nxt = wp.next(step)
        if not nxt:
            break
        wp = nxt[0]
        d = wp.transform.location.distance(LOADING_BAY)
        # Stop at the loading bay, or as soon as the lane moves away from it again: an earlier version
        # only stopped within `step` of it, missed it by a lane, and dressed half of Town05.
        if d > closest + 1.0:
            break
        closest = min(closest, d)
        out.append(wp)
        if d < step:
            break
    return out


def opposite(m: carla.Map, wp: carla.Waypoint, across: float) -> carla.Waypoint | None:
    """A driving waypoint on the other carriageway, `across` metres left of lane -1, heading the other way."""
    loc = off(wp.transform, right=-across)
    o = m.get_waypoint(loc, project_to_road=True, lane_type=carla.LaneType.Driving)
    if o is None:
        return None
    f1, f2 = wp.transform.get_forward_vector(), o.transform.get_forward_vector()
    return o if f1.x * f2.x + f1.y * f2.y < -0.5 else None


class Dresser:
    def __init__(self, world: carla.World):
        self.world, self.lib, self.spawned, self.failed = world, world.get_blueprint_library(), [], 0

    def put(self, bp_id: str, loc: carla.Location, yaw: float, colour: str | None = None):
        bp = self.lib.find(bp_id)
        if bp.has_attribute("role_name"):
            bp.set_attribute("role_name", TAG)
        if colour and bp.has_attribute("color"):
            bp.set_attribute("color", colour)
        actor = self.world.try_spawn_actor(bp, carla.Transform(loc, carla.Rotation(yaw=yaw)))
        if actor is None:
            self.failed += 1
            return None
        if bp_id.startswith("vehicle."):
            actor.set_simulate_physics(False)          # scenery: costs nothing, never moves
        self.spawned.append(actor)
        return actor

    def put_mesh(self, mesh: str, centre: carla.Location, yaw: float, pivot: tuple[float, float]):
        """A static mesh from CARLA's own content (static.prop.mesh), placed by its centre: a mesh's
        pivot is not at its middle, `pivot` is where the middle sits from the pivot (bounding box)."""
        bp = self.lib.find("static.prop.mesh")
        bp.set_attribute("mesh_path", f"{mesh}.{mesh.rsplit('/', 1)[-1]}")
        bp.set_attribute("role_name", TAG)
        c, sn = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        loc = carla.Location(centre.x - (c * pivot[0] - sn * pivot[1]), centre.y - (sn * pivot[0] + c * pivot[1]),
                             centre.z)
        actor = self.world.try_spawn_actor(bp, carla.Transform(loc, carla.Rotation(yaw=yaw)))
        if actor is None:
            self.failed += 1
            return None
        self.spawned.append(actor)
        return actor


def clear(world: carla.World, samples: list[carla.Waypoint]) -> int:
    """Remove an earlier run's dressing: tagged actors, and our prop types along the plant road."""
    near = lambda a: any(a.get_location().distance(s.transform.location) < 45 for s in samples[::3])
    gone = 0
    for a in world.get_actors():
        tagged = a.attributes.get("role_name") == TAG
        if tagged or (a.type_id in PROP_TYPES and near(a)):
            a.destroy()
            gone += 1
    return gone


def dress(world: carla.World, m: carla.Map, samples: list[carla.Waypoint]) -> Dresser:
    d = Dresser(world)
    for i, wp in enumerate(samples):
        y = wp.transform.location.y
        yaw = wp.transform.rotation.yaw
        shoulder = lane(wp, -4)
        # (a fence along the sidewalk was tried: the road already has a stone railing, which hid it)
        if shoulder is not None and i % 2 == 0 and (GATE_Y[1] <= y <= GATE_Y[0] or TRACK_Y[1] <= y <= TRACK_Y[0]):
            d.put("static.prop.trafficcone01", off(shoulder.transform, right=1.2, up=0.05), yaw)
        # the other carriageway: trucks along the straight, finished cars at the curve
        far = opposite(m, wp, 9.0)
        if far is not None:
            outer = far
            while outer.get_right_lane() is not None and outer.get_right_lane().lane_type == carla.LaneType.Driving:
                outer = outer.get_right_lane()
            if y > -120:
                # the straight: trucks in the outer lane, finished cars in the inner two, so the far
                # carriageway reads as a storage area and the only open road is the plant's own
                if i % 3 == 0:
                    d.put("vehicle.carlamotors.european_hgv", off(outer.transform, up=0.3), outer.transform.rotation.yaw)
                if i % 2 == 0:
                    lw = outer.get_left_lane()
                    for k in range(2):
                        if lw is None or lw.lane_type != carla.LaneType.Driving or lw.lane_id * outer.lane_id <= 0:
                            break
                        d.put("vehicle.lincoln.mkz_2020", off(lw.transform, up=0.3), lw.transform.rotation.yaw,
                              PLANT_COLOURS[(i + k) % len(PLANT_COLOURS)])
                        lw = lw.get_left_lane()
            elif y <= -128 and i % 2 == 0:
                inner = outer.get_left_lane()
                for k, lw in enumerate([outer, inner]):
                    if lw is not None and lw.lane_type == carla.LaneType.Driving:
                        d.put("vehicle.lincoln.mkz_2020", off(lw.transform, up=0.3), lw.transform.rotation.yaw,
                              PLANT_COLOURS[(i + k) % len(PLANT_COLOURS)])
    # the line end: stock waiting to go out
    start = lane(samples[0], -6)
    if start is not None:
        for j, (ahead, prop) in enumerate([(-2, "static.prop.box01"), (-1, "static.prop.box02"), (0, "static.prop.box03"),
                                           (1.5, "static.prop.barrel"), (2.2, "static.prop.barrel")]):
            d.put(prop, off(start.transform, ahead=ahead, up=0.1), start.transform.rotation.yaw)
    # the gate: calibration boards either side of the mark, a warning board before it
    gate = min(samples, key=lambda s: abs(s.transform.location.y + 33.93))
    gside = lane(gate, -6)
    if gside is not None:
        for ahead in (-4.0, 4.0):
            d.put("static.prop.calibrator", off(gside.transform, ahead=ahead, up=0.6), gside.transform.rotation.yaw + 90)
        d.put("static.prop.trafficwarning", off(gside.transform, ahead=-10, up=0.1), gside.transform.rotation.yaw)
    # the rework bays: workshop clutter on the sidewalk beside each bay
    for name, (x, y) in BAYS.items():
        bay = m.get_waypoint(carla.Location(x, y, 0.5), lane_type=carla.LaneType.Shoulder)
        bside = lane(bay, -6) if bay is not None else None
        if bside is None:
            continue
        t = bside.transform
        d.put("static.prop.barrel", off(t, ahead=-2.5, right=0.6, up=0.1), t.rotation.yaw)
        d.put("static.prop.barrel", off(t, ahead=-1.8, right=0.9, up=0.1), t.rotation.yaw)
        d.put("static.prop.box02", off(t, ahead=2.0, right=0.7, up=0.1), t.rotation.yaw)
        d.put("static.prop.ironplank", off(t, ahead=0.3, right=0.8, up=0.05), t.rotation.yaw)
        d.put("static.prop.warningconstruction", off(t, ahead=3.5, right=1.0, up=0.1), t.rotation.yaw + 180)
    return d


def check(m: carla.Map, samples: list[carla.Waypoint], actors) -> list[str]:
    """Nothing may stand on a lane of the plant's carriageway that a car drives or parks on."""
    heading = {s.road_id: s.transform.get_forward_vector() for s in samples}
    bad = []
    for a in actors:
        loc = a.get_location()
        wp = m.get_waypoint(loc, project_to_road=False, lane_type=carla.LaneType.Driving | carla.LaneType.Shoulder)
        if wp is None or wp.lane_width < 1.0:          # off the road, or the thin strip beside the shoulder
            continue
        if wp.lane_type == carla.LaneType.Shoulder and loc.y > FREE_SHOULDER_FROM_Y:
            continue                                   # shoulder before the first bay: no car stops there
        f = wp.transform.get_forward_vector()
        h = heading.get(wp.road_id)
        same_way = h is not None and f.x * h.x + f.y * h.y > 0.5
        near = min(loc.distance(s.transform.location) for s in samples) < 15
        if same_way and near:
            bad.append(f"{a.type_id} at ({loc.x:.1f}, {loc.y:.1f}) is on lane {wp.lane_id} ({wp.lane_type})")
    return bad


# ---- the plant's own world (scripts/plant_xodr.py): placed by (road, lane, s) ----
# Roads 1-8 are the loop: 1 south straight (line end to report point), 3 east (the bays), 5 north,
# 7 west (the yard); 2, 4, 6, 8 the bends. Lanes -1, -2 are driven, -3 holds the bays and slots,
# -4 is a walkway, -5 an apron or (road 7) the lot row, -6 the outer apron; lane 1 the inner apron.
# Road 9 paves the middle.
LOOP_ROADS = range(1, 9)


def at(m: carla.Map, road: int, lane: int, s: float, right: float = 0.0, ahead: float = 0.0, up: float = 0.1):
    wp = m.get_waypoint_xodr(road, lane, s)
    return (off(wp.transform, right=right, ahead=ahead, up=up), wp.transform.rotation.yaw) if wp else (None, 0.0)


def road_lengths() -> dict[int, float]:
    """From the plant's own road file: the paving road has no driving lane, so no waypoints to measure."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import plant_xodr as px
    lengths = {rid: (length if kind == "line" else math.pi / 2 * length) for rid, kind, length in px.ROADS}
    lengths[9] = px.STRAIGHT_LONG + 4.0
    return lengths


def dress_world(world: carla.World, m: carla.Map) -> Dresser:
    d = Dresser(world)

    def put(bp, road, lane, s, colour=None, yaw_add=0.0, **kw):
        loc, yaw = at(m, road, lane, s, **kw)
        if loc is not None:
            d.put(bp, loc, yaw + yaw_add, colour)

    lengths = road_lengths()
    for road in LOOP_ROADS:                            # the perimeter fence on the outer apron's edge
        s = 1.0
        while s < lengths[road] - 1.0:
            put("static.prop.streetbarrier", road, -6, s, right=3.2)
            s += 4.0
    # the yard: the lot row beside the slots, and rows on the inner apron opposite
    for k, s in enumerate(range(4, int(lengths[7]) - 2, 6)):
        put("vehicle.lincoln.mkz_2020", 7, -5, float(s), PLANT_COLOURS[k % 5], up=0.3)
        for row, right in enumerate((-4.0, 4.0)):             # two rows across the 20 m inner apron
            put("vehicle.lincoln.mkz_2020", 7, 1, float(s), PLANT_COLOURS[(k + row + 1) % 5], up=0.3, right=right)
    # the loading area: trucks along the north straight's inner apron
    for s in range(8, int(lengths[5]) - 6, 13):
        put("vehicle.carlamotors.european_hgv", 5, 1, float(s), up=0.3, right=4.0)
    # the middle: the storage compound, rows of finished cars
    for k, s in enumerate(range(10, int(lengths[9]) - 6, 9)):
        for row, (lane, right) in enumerate(((1, -5.0), (1, 5.0), (-1, -5.0), (-1, 5.0))):   # two rows a side
            put("vehicle.lincoln.mkz_2020", 9, lane, float(s), PLANT_COLOURS[(k + row) % 5], up=0.3, right=right)
    # the line end: stock waiting to go out
    for ahead, prop in [(2, "static.prop.box01"), (3, "static.prop.box02"), (4, "static.prop.box03"),
                        (6, "static.prop.barrel"), (6.7, "static.prop.barrel")]:
        put(prop, 1, -4, float(ahead))
    # the gate: calibration boards either side, a warning board before it
    for s in (28.0, 36.0):
        put("static.prop.calibrator", 1, -4, s, yaw_add=90.0, up=0.6)
    put("static.prop.trafficwarning", 1, -5, 22.0)
    # the test track: cones along the walkway
    for s in range(40, 96, 5):
        put("static.prop.trafficcone01", 1, -4, float(s), right=-0.8, up=0.05)
    detail_world(d, m, put, lengths)
    # the rework bays: workshop clutter on the walkway beside each
    for s in (8.0, 18.0, 28.0, 38.0, 50.0):
        put("static.prop.barrel", 3, -4, s - 2.5, right=0.4)
        put("static.prop.barrel", 3, -4, s - 1.8, right=0.7)
        put("static.prop.box02", 3, -4, s + 2.0, right=0.5)
        put("static.prop.ironplank", 3, -5, s + 0.3)
        put("static.prop.warningconstruction", 3, -5, s + 3.5, yaw_add=180.0)
    buildings_world(d, m, lengths)                    # last: see buildings_world
    return d


def detail_world(d: "Dresser", m: carla.Map, put, lengths: dict) -> None:
    """What makes it read as a working plant rather than a parked-car display: dividers between the
    lot rows, service vans at the bays, ground clutter, and (MARSHAL_DRESS_PEOPLE, default on) a few
    staff standing in the yard, the compound and by the bays - away from every driven lane, because a
    person in view is what makes the assistance card recommend waiting."""
    import os
    import random
    rng = random.Random(7)
    # dividers between the compound's rows and between the yard's rows on the inner apron
    for s in range(6, int(lengths[9]) - 4, 4):
        for lane in (1, -1):
            put("static.prop.streetbarrier", 9, lane, float(s))
    for s in range(3, int(lengths[7]) - 2, 4):
        put("static.prop.streetbarrier", 7, 1, float(s))
    # Markings. CARLA's OpenDRIVE world is one flat concrete colour with no painted lines, and it ignores
    # lane heights and superelevation, so the ground cannot be shaped either (both tried). Its iron plate
    # is a flat rust-brown 1.45 m square: laid as dashed lines along the parking rows they mark out the
    # bays, and in patches they break up the open aprons. At the road's own height a plate is buried in
    # it and invisible (the sidewalk lanes are raised like a kerb), so they sit 22 cm up.
    plate = "static.prop.ironplank"
    for s in range(2, int(lengths[7]) - 1, 3):           # the yard: both edges of the lot row, the inner rows
        put(plate, 7, -5, float(s), right=-2.6, up=0.22)
        put(plate, 7, -5, float(s), right=2.6, up=0.22)
        put(plate, 7, 1, float(s), right=-7.0, up=0.22)
        put(plate, 7, 1, float(s), right=7.0, up=0.22)
    for s in range(4, int(lengths[9]) - 2, 3):           # the compound: the outer edge of each pair of rows
        for lane in (1, -1):
            put(plate, 9, lane, float(s), right=-8.0, up=0.22)
            put(plate, 9, lane, float(s), right=8.0, up=0.22)
    for _ in range(12):                                   # worn patches on the aprons
        road = rng.choice(list(LOOP_ROADS))
        s0 = rng.uniform(4.0, lengths[road] - 6.0)
        lane, right0 = rng.choice([(-6, rng.uniform(-2.0, 2.0)), (-7, rng.uniform(-15.0, 15.0)),
                                   (1, rng.uniform(-6.0, 6.0))])
        for i in range(2):
            for j in range(2):
                put(plate, road, lane, s0 + 1.45 * i, right=right0 + 1.17 * j, up=0.22)
    # service vans on the apron beside the bays
    for s in (8.0, 28.0, 50.0):
        put("vehicle.mercedes.sprinter", 3, -5, s + 1.0, "230,230,230", up=0.3)
    # ground clutter on the aprons, so the concrete is not one flat colour
    clutter = ["static.prop.dirtdebris01", "static.prop.dirtdebris02", "static.prop.dirtdebris03",
               "static.prop.brokentile01", "static.prop.brokentile02", "static.prop.brokentile03"]
    for _ in range(40):
        road = rng.choice(list(LOOP_ROADS))
        s = rng.uniform(2.0, lengths[road] - 2.0)
        lane, right = rng.choice([(-6, rng.uniform(-3.5, 3.5)), (-7, rng.uniform(-18.0, 18.0)),
                                  (1, rng.uniform(-9.0, 9.0))])
        put(rng.choice(clutter), road, lane, s, right=right, up=0.02, yaw_add=rng.uniform(0, 360))
    if os.environ.get("MARSHAL_DRESS_PEOPLE", "1") == "0":
        return
    walkers = sorted(b.id for b in d.lib.filter("walker.pedestrian.*"))
    for k, (road, lane, s, right) in enumerate([(7, 1, 20.0, 0.0), (7, 1, 44.0, 0.0), (9, 1, 40.0, 0.0),
                                                 (9, -1, 82.0, 0.0), (3, -5, 20.0, 1.5), (3, -5, 42.0, 1.5)]):
        put(walkers[(k * 7) % len(walkers)], road, lane, s, right=right, up=1.0, yaw_add=90.0 * k)


# CARLA's factory halls (Town05's industrial estate), each about 50 m long: mesh -> (yaw to lay its
# long side along the road, where its middle sits from its pivot), measured from their bounding boxes
FACTORY = "/Game/Carla/Static/Building/Factory/SM_Factory_v0{}_Merged"
HALLS = {1: (90.0, (-7.5, 20.0)), 2: (0.0, (20.0, 11.7)), 3: (90.0, (11.5, -20.1))}
# road, s of the hall's middle, which hall: two along each long straight, one along each short one
HALL_SITES = [(1, 32.0, 2), (1, 90.0, 3), (3, 30.0, 1), (5, 30.0, 3), (5, 88.0, 2), (7, 30.0, 1)]


def buildings_world(d: "Dresser", m: carla.Map, lengths: dict) -> None:
    """Factory halls beyond the perimeter fence, on the 40 m of paving there (lane -7), along every
    straight and across every bend, so the loop runs between buildings like a plant's internal road
    rather than across an open lot; and trucks in front of the corner halls, as at loading docks.

    The halls are spawned after everything else. CARLA's replayer spawns each recorded actor 1 km
    above the origin and then moves it into place; a static mesh cannot be moved, so a replayed hall
    stays up there and every actor spawned after it collides and is not created, the filmed car
    included. Spawned last, the halls block nothing in the dressing, and replay_carla.py removes the
    stranded copies before the cars appear and places the halls again itself."""
    for road in (2, 4, 6, 8):
        for ahead in (-9.0, 9.0):
            loc, yaw = at(m, road, -7, lengths[road] / 2, right=-12.0, ahead=ahead, up=0.3)
            if loc is not None:
                d.put("vehicle.carlamotors.european_hgv", loc, yaw)
    plant_halls(d, m, lengths)


def plant_halls(d: "Dresser", m: carla.Map, lengths: dict) -> None:
    """The halls alone: replay_carla.py places them itself, as the replayer cannot."""
    for road, s, hall in HALL_SITES:
        loc, yaw = at(m, road, -7, s, up=0.2)
        if loc is not None:
            turn, pivot = HALLS[hall]
            d.put_mesh(FACTORY.format(hall), loc, yaw + turn, pivot)
    for road in (2, 4, 6, 8):                          # the corners: the slimmest hall, set back
        loc, yaw = at(m, road, -7, lengths[road] / 2, right=6.0, up=0.2)
        if loc is not None:
            turn, pivot = HALLS[1]
            d.put_mesh(FACTORY.format(1), loc, yaw + turn, pivot)


def check_world(m: carla.Map, actors) -> list[str]:
    """Nothing on a driven lane of the loop, or on the bays' and slots' shoulder."""
    bad = []
    for a in actors:
        loc = a.get_location()
        wp = m.get_waypoint(loc, project_to_road=False, lane_type=carla.LaneType.Any)
        if wp is not None and wp.road_id in LOOP_ROADS and wp.lane_id in (-1, -2, -3):
            bad.append(f"{a.type_id} at ({loc.x:.1f}, {loc.y:.1f}) is on road {wp.road_id} lane {wp.lane_id}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--clear", action="store_true", help="only remove the dressing")
    args = ap.parse_args()
    client = carla.Client(args.host, args.port)
    client.set_timeout(60.0)
    world = client.get_world()
    name = world.get_map().name
    if not name.endswith(("Town05", "Town05_Opt", "OpenDriveMap")):
        emit({"type": "dress.skipped", "reason": f"map is {name}, not Town05"})
        return 0
    if name.endswith("_Opt"):
        unloaded = strip_city(world)
        emit({"type": "dress.layers_unloaded", "layers": unloaded})
    m = world.get_map()
    if name.endswith("OpenDriveMap"):
        removed = clear(world, [])
        if args.clear:
            emit({"type": "dress.cleared", "removed": removed})
            return 0
        d = dress_world(world, m)
        try:
            world.wait_for_tick(10.0)
        except RuntimeError:
            pass
        bad = check_world(m, d.spawned)
        counts: dict[str, int] = {}
        for a in d.spawned:
            counts[a.type_id] = counts.get(a.type_id, 0) + 1
        emit({"type": "dress.done", "world": "plant", "removed_first": removed, "placed": len(d.spawned),
              "not_placed": d.failed, "by_type": counts, "on_a_plant_lane": bad})
        if bad:
            for a in d.spawned:
                a.destroy()
            return 1
        return 0
    samples = route_samples(m)
    removed = clear(world, samples)
    if args.clear:
        emit({"type": "dress.cleared", "removed": removed})
        return 0
    d = dress(world, m, samples)
    try:
        world.wait_for_tick(10.0)
    except RuntimeError:
        pass
    bad = check(m, samples, d.spawned)
    counts: dict[str, int] = {}
    for a in d.spawned:
        counts[a.type_id] = counts.get(a.type_id, 0) + 1
    emit({"type": "dress.done", "removed_first": removed, "placed": len(d.spawned), "not_placed": d.failed,
          "by_type": counts, "on_a_plant_lane": bad})
    if bad:
        for a in d.spawned:
            a.destroy()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
