#!/usr/bin/env python3
"""The plant's own road network as OpenDRIVE: a one-way loop shaped like the plant, not a city road.

Town05 reads as a highway however it is dressed (three wide lanes, a central barrier, street lamps,
a speed sign, houses), and its layered version crashes CARLA 0.9.16 when layers are unloaded. CARLA
can build a world from an OpenDRIVE file instead (client.generate_opendrive_world), with walls along
the road edges. This writes the plant as such a file:

    south straight   line end -> buffer -> inspection gate -> test track -> report point
    east straight    the five rework bays, on the outer shoulder
    north-east bend  the finishing corner (the lab's occluded-worker scenarios)
    north straight   finishing
    west straight    yard slots Y1-Y8 on the outer shoulder, then the loading bay
    and back round to the line end, so a reworked car can come round to the gate again
    lane C           a two-lane connector across the middle, from just past the report point to the
                     finishing straight, with a junction at each end: the other way to the yard,
                     which skips the finishing corner (flows/route.py chooses between them)

Every road has two driving lanes (-1 inner, -2 outer), a shoulder (-3) where cars park, and a
sidewalk; the loop runs counter-clockwise, so the shoulder is on the outside. Plant points are given
as (road, lane, s), resolved to CARLA transforms on the server (load_plant_world.py).

    python3 scripts/plant_xodr.py --out /tmp/marshal_plant.xodr
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

STRAIGHT_LONG, STRAIGHT_SHORT, RADIUS = 120.0, 60.0, 22.0
# CARLA builds the world from the lanes alone (no ground, no terrain), so anything that should look
# paved is a lane. Widths are the same on every road, so the surface joins up at the bends.
# Right of the reference line, in the direction of travel (the outside of the loop):
#   -1, -2  driving
#   -3      shoulder: the rework bays and, on the yard road, slots Y1-Y8 (parking there)
#   -4      a walkway, beside the slots on the yard road
#   -5      on the yard road a second row of parking, for the lot of finished cars; else apron
#   -6      the apron out to the perimeter fence (dress_plant.py)
#   -7      paving beyond the fence: without it the world ends at the fence, in grey nothing
RIGHT = [(-1, 3.5, "broken"), (-2, 3.5, "solid"), (-3, 3.5, "solid"), (-4, 3.0, "none"), (-5, 5.5, "solid"),
         (-6, 8.0, "none"), (-7, 40.0, "none")]
RIGHT_TYPES = {"default": ["driving", "driving", "shoulder", "sidewalk", "sidewalk", "sidewalk", "sidewalk"],
               # not a driving aisle: a driving lane linked to the next road's sidewalk is a dead end,
               # and the route planner, aiming for the nearest driving lane to a slot, found no path
               7: ["driving", "driving", "parking", "sidewalk", "parking", "sidewalk", "sidewalk"]}
INNER_APRON = 20.0          # left of the reference line, inside the loop (under the bend radius)
# The paving inside the loop is a shoulder, not a sidewalk: CARLA raises sidewalks 15 cm like a kerb,
# and lane C runs across the middle. It used to be sidewalk, with nothing driving there.
INNER_TYPE = "shoulder"
FILL_WIDTH = 32.0           # each side of the filler road across the middle of the loop

# road id -> (kind, length): four straights and four quarter bends, counter-clockwise
ROADS = [(1, "line", STRAIGHT_LONG), (2, "arc", RADIUS), (3, "line", STRAIGHT_SHORT), (4, "arc", RADIUS),
         (5, "line", STRAIGHT_LONG), (6, "arc", RADIUS), (7, "line", STRAIGHT_SHORT), (8, "arc", RADIUS)]

ARC = math.pi / 2 * RADIUS
# plant point -> (road, lane, s along the road)
POINTS = {
    "line_end": (1, -2, 8.0), "buffer_zone_b": (1, -2, 18.0), "inspection_gate": (1, -2, 32.0),
    "test_track": (1, -2, 62.0), "report_point": (1, -2, 100.0), "lane_c": (1, -1, 100.0),
    # in the order rework visits them (report.SAFETY_ORDER): on a one-way loop the next bay must be
    # ahead, or the car drives the whole loop to reach it
    "rework.mechanical": (3, -3, 8.0), "rework.electrical": (3, -3, 18.0), "rework.body": (3, -3, 28.0),
    "rework.paint": (3, -3, 38.0), "production_control": (3, -3, 50.0),
    "finishing_corner.p1": (4, -2, ARC * 0.15), "finishing_corner.p2": (4, -2, ARC * 0.5),
    "finishing_corner.p3": (4, -2, ARC * 0.85), "finishing": (5, -2, 30.0),
    "yard.entry": (7, -2, 2.0),
    # A car pulls into its slot from a run-up 12 m before it (driving/carla_backend.py
    # PULL_OVER_RUN_M). The first slot starts 14 m into the straight, so every run-up is straight:
    # from slots at 6 m the run-up began in the bend, the car parked across the lane, and the cars
    # after it stopped against it at the yard entry. 5.5 m apart for 4.9 m cars, the last clear
    # of the loading bay at 56 m.
    **{f"yard.Y{i}": (7, -3, 14.0 + 5.5 * (i - 1)) for i in range(1, 9)},
    "loading_bay": (7, -2, 56.0),
}
POINTS["lane_c"] = (20, -2, 40.0)       # halfway along the connector (was the inner lane beside the report point)

# Lane C: it leaves road 1 at s = LANE_C_FROM and joins road 5 at the same distance from road 5's end,
# turning through RADIUS_C at each end. Junction roads are JUNCTION_M long.
LANE_C_FROM, RADIUS_C, JUNCTION_M = 102.0, 10.0, 4.0
# A loop road split by a junction: logical road -> [(physical road, from s, to s)]. Everything that places
# things by (road, lane, s) on the loop - the plant points, the dressing - keeps the loop's own s; this
# table says which physical road that s is on now (physical()).
SPLITS = {
    1: [(1, 0.0, LANE_C_FROM), (101, LANE_C_FROM, LANE_C_FROM + JUNCTION_M),
        (11, LANE_C_FROM + JUNCTION_M, STRAIGHT_LONG)],
    5: [(5, 0.0, STRAIGHT_LONG - LANE_C_FROM - JUNCTION_M),
        (103, STRAIGHT_LONG - LANE_C_FROM - JUNCTION_M, STRAIGHT_LONG - LANE_C_FROM),
        (51, STRAIGHT_LONG - LANE_C_FROM, STRAIGHT_LONG)],
}


def physical(road: int, s: float) -> tuple[int, float]:
    """The physical road and s for a place given on the loop's own roads 1-8."""
    for rid, a, b in SPLITS.get(road, []):
        if a <= s <= b:
            return rid, s - a
    return road, s


def lane_c_corridor() -> tuple[float, float, float, float]:
    """(x0, x1, y0, y1) in CARLA coordinates that dressing must keep clear of: lane C and its turns."""
    x0 = LANE_C_FROM - 2.0
    x1 = LANE_C_FROM + RADIUS_C + 2 * 3.5 + 3.0
    return x0, x1, -(STRAIGHT_SHORT + 2 * RADIUS + 2 * 3.5 + 2.0), 9.0      # to road 5's far lane


def geometry() -> list[tuple[int, str, float, float, float, float]]:
    """(road, kind, x, y, heading, length) for each road, chained end to start."""
    x, y, hdg, out = 0.0, 0.0, 0.0, []
    for rid, kind, length in ROADS:
        if kind == "line":
            out.append((rid, kind, x, y, hdg, length))
            x, y = x + length * math.cos(hdg), y + length * math.sin(hdg)
        else:
            arc_len = math.pi / 2 * length                  # `length` is the radius for a bend
            out.append((rid, kind, x, y, hdg, arc_len))
            cx, cy = x - length * math.sin(hdg), y + length * math.cos(hdg)   # left turn: centre on the left
            hdg += math.pi / 2
            x, y = cx + length * math.sin(hdg), cy - length * math.cos(hdg)
    return out


def lane_xml(lane_id: int, kind: str, width: float, mark: str, linked: bool = True) -> str:
    road_mark = ("" if mark == "none" else
                 f'<roadMark sOffset="0" type="{mark}" weight="standard" color="standard" width="0.12"/>')
    link = f'<link><predecessor id="{lane_id}"/><successor id="{lane_id}"/></link>' if linked else ""
    return (f'<lane id="{lane_id}" type="{kind}" level="false">{link}'
            f'<width sOffset="0" a="{width}" b="0" c="0" d="0"/>{road_mark}</lane>')


def road_xml(rid: int, name: str, length: float, x: float, y: float, hdg: float, shape: str, link: str,
             left: str, right: str) -> str:
    return (f'<road name="{name}" length="{length:.4f}" id="{rid}" junction="-1">{link}'
            f'<type s="0" type="town"><speed max="20" unit="km/h"/></type>'
            f'<planView><geometry s="0" x="{x:.4f}" y="{y:.4f}" hdg="{hdg:.6f}" length="{length:.4f}">{shape}</geometry></planView>'
            f'<elevationProfile><elevation s="0" a="0" b="0" c="0" d="0"/></elevationProfile><lateralProfile/>'
            f'<lanes><laneSection s="0">{left}<center><lane id="0" type="none" level="false">'
            f'<roadMark sOffset="0" type="solid" weight="standard" color="standard" width="0.12"/></lane></center>'
            f'{right}</laneSection></lanes></road>')


def _lanes(rid: int, left_apron: bool = True) -> tuple[str, str]:
    types = RIGHT_TYPES.get(rid, RIGHT_TYPES["default"])
    right = "<right>" + "".join(lane_xml(lid, t, w, mark) for (lid, w, mark), t in zip(RIGHT, types)) + "</right>"
    left = "<left>" + lane_xml(1, INNER_TYPE, INNER_APRON, "none") + "</left>" if left_apron else ""
    return left, right


def _link(pred: tuple[str, int, str] | None, succ: tuple[str, int, str] | None) -> str:
    def one(tag, ref):
        kind, rid, contact = ref
        cp = f' contactPoint="{contact}"' if kind == "road" else ""
        return f'<{tag} elementType="{kind}" elementId="{rid}"{cp}/>'
    return "<link>" + (one("predecessor", pred) if pred else "") + (one("successor", succ) if succ else "") + "</link>"


def _road(rid, name, length, x, y, hdg, shape, link, left, right, junction=-1) -> str:
    return road_xml(rid, name, length, x, y, hdg, shape, link, left, right).replace(
        f'id="{rid}" junction="-1"', f'id="{rid}" junction="{junction}"', 1)


DRIVEN = [(-1, 3.5, "broken"), (-2, 3.5, "solid")]


def xodr() -> str:
    """The loop, with roads 1 and 5 split where lane C leaves and joins them, and lane C."""
    geo = {rid: (kind, x, y, hdg, length) for rid, kind, x, y, hdg, length in geometry()}
    roads = []

    def loop_road(rid, x, y, hdg, length, kind, pred, succ, name=None, junction=-1):
        shape = "<line/>" if kind == "line" else f'<arc curvature="{1.0 / RADIUS:.6f}"/>'
        left, right = _lanes(rid if rid <= 8 else {11: 1, 101: 1, 51: 5, 103: 5}[rid])
        roads.append(_road(rid, name or f"plant_{rid}", length, x, y, hdg, shape, _link(pred, succ), left,
                           right, junction))

    r = lambda rid, contact: ("road", rid, contact)
    j = lambda jid: ("junction", jid, "")
    # the loop: 1 (to lane C) -> J100 -> 11 -> 2 -> 3 -> 4 -> 5 -> J200 -> 51 -> 6 -> 7 -> 8 -> 1
    _k, x1, y1, h1, _l = geo[1]
    loop_road(1, x1, y1, h1, LANE_C_FROM, "line", r(8, "end"), j(100))
    loop_road(101, x1 + LANE_C_FROM, y1, h1, JUNCTION_M, "line", r(1, "end"), r(11, "start"),
              name="plant_1_junction", junction=100)
    loop_road(11, x1 + LANE_C_FROM + JUNCTION_M, y1, h1, STRAIGHT_LONG - LANE_C_FROM - JUNCTION_M, "line",
              j(100), r(2, "start"), name="plant_1b")
    for rid, pred, succ in ((2, 11, 3), (3, 2, 4), (4, 3, 5), (6, 51, 7), (7, 6, 8), (8, 7, 1)):
        kind, x, y, hdg, length = geo[rid]
        if rid == 4:
            loop_road(4, x, y, hdg, length, kind, r(3, "end"), r(5, "start"))
        else:
            loop_road(rid, x, y, hdg, length, kind, r(pred, "end"), r(succ, "start"))
    _k, x5, y5, h5, _l = geo[5]                      # heading west from (120, 104)
    a5 = STRAIGHT_LONG - LANE_C_FROM - JUNCTION_M
    loop_road(5, x5, y5, h5, a5, "line", r(4, "end"), j(200))
    loop_road(103, x5 - a5, y5, h5, JUNCTION_M, "line", r(5, "end"), r(51, "start"),
              name="plant_5_junction", junction=200)
    loop_road(51, x5 - a5 - JUNCTION_M, y5, h5, LANE_C_FROM, "line", j(200), r(6, "start"), name="plant_5b")

    # lane C: a left turn off road 1, straight north across the middle, a left turn onto road 5
    turn = math.pi / 2 * RADIUS_C
    driven = "<right>" + "".join(lane_xml(lid, "driving", w, mark) for lid, w, mark in DRIVEN) + "</right>"
    curve = f'<arc curvature="{1.0 / RADIUS_C:.6f}"/>'
    cx = x1 + LANE_C_FROM + RADIUS_C                 # lane C's reference line, x
    roads.append(_road(102, "lane_c_in", turn, x1 + LANE_C_FROM, y1, 0.0, curve,
                       _link(r(1, "end"), r(20, "start")), "", driven, 100))
    length_c = y5 - y1 - 2 * RADIUS_C
    roads.append(_road(20, "lane_c", length_c, cx, y1 + RADIUS_C, math.pi / 2, "<line/>",
                       _link(j(100), j(200)), "", driven))
    roads.append(_road(104, "lane_c_out", turn, cx, y5 - RADIUS_C, math.pi / 2, curve,
                       _link(r(20, "end"), r(51, "start")), "", driven, 200))
    all_lanes = [lid for lid, _w, _m in RIGHT] + [1]
    junctions = (
        '<junction id="100" name="lane_c_from_road_1">'
        '<connection id="0" incomingRoad="1" connectingRoad="101" contactPoint="start">'
        + "".join(f'<laneLink from="{l}" to="{l}"/>' for l in all_lanes) + '</connection>'
        '<connection id="1" incomingRoad="1" connectingRoad="102" contactPoint="start">'
        '<laneLink from="-1" to="-1"/><laneLink from="-2" to="-2"/></connection></junction>'
        '<junction id="200" name="lane_c_to_road_5">'
        '<connection id="0" incomingRoad="5" connectingRoad="103" contactPoint="start">'
        + "".join(f'<laneLink from="{l}" to="{l}"/>' for l in all_lanes) + '</connection>'
        '<connection id="1" incomingRoad="20" connectingRoad="104" contactPoint="start">'
        '<laneLink from="-1" to="-1"/><laneLink from="-2" to="-2"/></connection></junction>')
    # the middle of the loop: paving nothing drives on, so the plant is not an island in a void
    cy = STRAIGHT_SHORT / 2 + RADIUS
    roads.append(road_xml(9, "plant_floor", STRAIGHT_LONG + 4.0, -2.0, cy, 0.0, "<line/>", "",
                          "<left>" + lane_xml(1, INNER_TYPE, FILL_WIDTH, "none", linked=False) + "</left>",
                          "<right>" + lane_xml(-1, INNER_TYPE, FILL_WIDTH, "none", linked=False) + "</right>"))
    return ('<?xml version="1.0" standalone="yes"?>\n<OpenDRIVE><header revMajor="1" revMinor="4" '
            'name="marshal_plant" version="1" north="0" south="0" east="0" west="0"/>'
            + "".join(roads) + junctions + "</OpenDRIVE>\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("/tmp/marshal_plant.xodr"))
    args = ap.parse_args()
    args.out.write_text(xodr())
    args.out.with_suffix(".points.json").write_text(json.dumps(POINTS, indent=1))
    print(json.dumps({"xodr": str(args.out), "roads": len(ROADS), "points": len(POINTS),
                      "loop_m": round(2 * (STRAIGHT_LONG + STRAIGHT_SHORT) + 4 * ARC, 1)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
