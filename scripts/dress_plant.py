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
PROP_TYPES = ("static.prop.chainbarrier", "static.prop.trafficcone01", "static.prop.box01", "static.prop.box02",
              "static.prop.box03", "static.prop.barrel", "static.prop.ironplank", "static.prop.warningconstruction",
              "static.prop.calibrator", "static.prop.trafficwarning", "static.prop.streetbarrier")


# On the layered map (Town05_Opt, the same roads), the city's own scenery is in layers that can be
# taken away: the houses and the skyline, the highway's lamp posts, its signs and street furniture,
# parked cars. What is left is the roads, the ground and the trees, and the dressing becomes the only
# built thing in view. MARSHAL_PLANT_UNLOAD overrides the list. Not yet checked on the server: loading
# another map there changes it for everyone, so it waits for a free window.
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
    if not name.endswith(("Town05", "Town05_Opt")):
        emit({"type": "dress.skipped", "reason": f"map is {name}, not Town05"})
        return 0
    if name.endswith("_Opt"):
        unloaded = strip_city(world)
        emit({"type": "dress.layers_unloaded", "layers": unloaded})
    m = world.get_map()
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
