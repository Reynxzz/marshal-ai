"""Road network for MetaDrive and Scenic, built from the plant map.

MetaDrive draws and localises lanes from a SUMO network (`.net.xml`); Scenic reads the
same roads from OpenDRIVE (`.xodr`). Both come from one netconvert run over the plant
map, so the three agree on coordinates:

    plant map (x, y)  ==  SUMO/OpenDRIVE (x, y)  ==  Scenic (x, y)  ==  MetaDrive (x, y) + map centre

Chains of plant segments become roads, points where roads meet become junctions, and
yard slots stay off-road: the yard is a parking area beside the aisle to the loading bay.

    python -m marshal_ai.plant.roadnet        # rebuild plant/metadrive/ after editing the map
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from marshal_ai.plant.map import PlantMap, load_plant_map

NET_DIR = Path(__file__).with_name("metadrive")
NET_FILE = NET_DIR / "plant.net.xml"
XODR_FILE = NET_DIR / "plant.xodr"
LANE_WIDTH_M = 3.5
SPEED_MS = 10 / 3.6


def off_road(name: str) -> bool:
    return name.startswith("yard.Y")


@dataclass(frozen=True)
class Road:
    points: tuple[str, ...]          # plant points along the road, first and last are junctions
    one_way: bool

def _nid(name: str) -> str:
    return name.replace(".", "_")


def edge_id(pts: tuple[str, ...]) -> str:
    """Unique per road and direction: two roads can join the same junctions (the corner and lane C)."""
    names = (pts[0], pts[1], pts[-1]) if len(pts) > 2 else (pts[0], pts[-1])
    return "__".join(_nid(p) for p in names)


def roads(m: PlantMap) -> list[Road]:
    """Collapse the plant graph into roads between junctions."""
    pairs = {(a, b) for a, nxt in m.edges.items() for b in nxt if not (off_road(a) or off_road(b))}
    one_way = {(a, b) for a, b in pairs if (b, a) not in pairs}
    neighbours: dict[str, set[str]] = {}
    for a, b in pairs:
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    junctions = {p for p, n in neighbours.items() if len(n) != 2}
    # a point between a one-way and a two-way segment must be a junction too
    for p, n in neighbours.items():
        if len({((p, q) in one_way or (q, p) in one_way) for q in n}) > 1:
            junctions.add(p)

    out, seen = [], set()
    for start in sorted(junctions):
        for first in sorted(neighbours[start]):
            if (start, first) in seen:
                continue
            chain = [start, first]
            while chain[-1] not in junctions:
                chain.append(next(q for q in neighbours[chain[-1]] if q != chain[-2]))
            seen.update(zip(chain, chain[1:]))
            seen.update(zip(chain[1:], chain))
            ow = (start, first) in one_way or (first, start) in one_way
            if ow and (start, first) not in pairs:
                chain.reverse()
            out.append(Road(tuple(chain), ow))
    return out


def plain_xml(m: PlantMap) -> tuple[str, str]:
    rs = roads(m)
    junctions = sorted({p for r in rs for p in (r.points[0], r.points[-1])})
    nodes = "\n".join(f'  <node id="{_nid(p)}" x="{m.points[p].x}" y="{m.points[p].y}"/>'
                      for p in junctions)
    edges = []
    for r in rs:
        variants = [r.points] if r.one_way else [r.points, r.points[::-1]]
        for pts in variants:
            shape = " ".join(f"{m.points[p].x},{m.points[p].y}" for p in pts)
            edges.append(f'  <edge id="{edge_id(pts)}" from="{_nid(pts[0])}" '
                         f'to="{_nid(pts[-1])}" numLanes="1" speed="{SPEED_MS:.2f}" '
                         f'width="{LANE_WIDTH_M}" shape="{shape}"/>')
    return f"<nodes>\n{nodes}\n</nodes>\n", "<edges>\n" + "\n".join(edges) + "\n</edges>\n"


def build(m: PlantMap | None = None, out_dir: Path = NET_DIR) -> tuple[Path, Path]:
    m = m or load_plant_map()
    netconvert = shutil.which("netconvert") or str(Path(sys.executable).with_name("netconvert"))
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes, edges = plain_xml(m)
    with tempfile.TemporaryDirectory() as tmp:
        nod, edg = Path(tmp, "plant.nod.xml"), Path(tmp, "plant.edg.xml")
        nod.write_text(nodes)
        edg.write_text(edges)
        net, xodr = out_dir / NET_FILE.name, out_dir / XODR_FILE.name
        subprocess.run([netconvert, "-n", str(nod), "-e", str(edg), "-o", str(net),
                        "--opendrive-output", str(xodr), "--no-turnarounds", "true",
                        "--default.junctions.radius", "6", "--offset.disable-normalization", "true"],
                       check=True, capture_output=True)
    return net, xodr


def net_offset(net_file: Path = NET_FILE) -> tuple[float, float]:
    """Built with normalisation off, so this is (0, 0): plant and network coordinates are the same."""
    loc = ET.parse(net_file).getroot().find("location")
    x, y = map(float, loc.attrib["netOffset"].split(","))
    return x, y


if __name__ == "__main__":
    for path in build():
        print(path)
