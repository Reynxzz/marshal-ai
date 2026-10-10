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
#   -6      the apron out to the perimeter wall
RIGHT = [(-1, 3.5, "broken"), (-2, 3.5, "solid"), (-3, 3.5, "solid"), (-4, 3.0, "none"), (-5, 5.5, "solid"),
         (-6, 8.0, "none")]
RIGHT_TYPES = {"default": ["driving", "driving", "shoulder", "sidewalk", "sidewalk", "sidewalk"],
               # not a driving aisle: a driving lane linked to the next road's sidewalk is a dead end,
               # and the route planner, aiming for the nearest driving lane to a slot, found no path
               7: ["driving", "driving", "parking", "sidewalk", "parking", "sidewalk"]}
INNER_APRON = 20.0          # left of the reference line, inside the loop (under the bend radius)
FILL_WIDTH = 32.0           # each side of the filler road across the middle of the loop

# road id -> (kind, length): four straights and four quarter bends, counter-clockwise
ROADS = [(1, "line", STRAIGHT_LONG), (2, "arc", RADIUS), (3, "line", STRAIGHT_SHORT), (4, "arc", RADIUS),
         (5, "line", STRAIGHT_LONG), (6, "arc", RADIUS), (7, "line", STRAIGHT_SHORT), (8, "arc", RADIUS)]

ARC = math.pi / 2 * RADIUS
# plant point -> (road, lane, s along the road)
POINTS = {
    "line_end": (1, -2, 8.0), "buffer_zone_b": (1, -2, 18.0), "inspection_gate": (1, -2, 32.0),
    "test_track": (1, -2, 62.0), "report_point": (1, -2, 100.0), "lane_c": (1, -1, 100.0),
    "rework.electrical": (3, -3, 8.0), "rework.mechanical": (3, -3, 18.0), "rework.body": (3, -3, 28.0),
    "rework.paint": (3, -3, 38.0), "production_control": (3, -3, 50.0),
    "finishing_corner.p1": (4, -2, ARC * 0.15), "finishing_corner.p2": (4, -2, ARC * 0.5),
    "finishing_corner.p3": (4, -2, ARC * 0.85), "finishing": (5, -2, 30.0),
    "yard.entry": (7, -2, 2.0),
    **{f"yard.Y{i}": (7, -3, 6.0 + 6.0 * (i - 1)) for i in range(1, 9)},
    "loading_bay": (7, -2, 56.0),
}


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


def xodr() -> str:
    roads = []
    geo = geometry()
    n = len(geo)
    for i, (rid, kind, x, y, hdg, length) in enumerate(geo):
        prev_id, next_id = geo[i - 1][0], geo[(i + 1) % n][0]
        shape = "<line/>" if kind == "line" else f'<arc curvature="{1.0 / RADIUS:.6f}"/>'
        types = RIGHT_TYPES.get(rid, RIGHT_TYPES["default"])
        right = "<right>" + "".join(lane_xml(lid, t, w, mark) for (lid, w, mark), t in zip(RIGHT, types)) + "</right>"
        left = "<left>" + lane_xml(1, "sidewalk", INNER_APRON, "none") + "</left>"
        link = (f'<link><predecessor elementType="road" elementId="{prev_id}" contactPoint="end"/>'
                f'<successor elementType="road" elementId="{next_id}" contactPoint="start"/></link>')
        roads.append(road_xml(rid, f"plant_{rid}", length, x, y, hdg, shape, link, left, right))
    # the middle of the loop: a paved road nothing drives on, so the plant is not an island in a void
    cy = STRAIGHT_SHORT / 2 + RADIUS
    roads.append(road_xml(9, "plant_floor", STRAIGHT_LONG + 4.0, -2.0, cy, 0.0, "<line/>", "",
                          "<left>" + lane_xml(1, "sidewalk", FILL_WIDTH, "none", linked=False) + "</left>",
                          "<right>" + lane_xml(-1, "sidewalk", FILL_WIDTH, "none", linked=False) + "</right>"))
    return ('<?xml version="1.0" standalone="yes"?>\n<OpenDRIVE><header revMajor="1" revMinor="4" '
            'name="marshal_plant" version="1" north="0" south="0" east="0" west="0"/>'
            + "".join(roads) + "</OpenDRIVE>\n")


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
