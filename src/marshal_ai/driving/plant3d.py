"""Plant road surface for MetaDrive's 3D window.

MetaDrive paints roads onto its terrain with GLSL 3.30 shaders, but macOS only gives Panda3D a legacy
OpenGL 2.1 context (and MetaDrive's other shaders break on the modern one), so on a Mac the ground
stays blank. This draws the plant instead, as plain geometry: asphalt from the SUMO lane and junction
shapes, a centre line on two-way roads, and outlines for yard slots and rework bays.
"""

from __future__ import annotations

import math
from pathlib import Path

import shapely
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from marshal_ai.plant import PlantMap
from marshal_ai.plant.roadnet import NET_FILE, off_road

ASPHALT = (0.32, 0.33, 0.35, 1.0)
PAINT = (0.92, 0.92, 0.88, 1.0)
BAY_PAINT = (0.95, 0.78, 0.15, 1.0)
LINE_M = 0.15
SLOT_LENGTH_M, SLOT_WIDTH_M = 5.4, 2.8


def _polygons(geom) -> list[Polygon]:
    if geom.is_empty:
        return []
    return list(geom.geoms) if hasattr(geom, "geoms") else [geom]


def surfaces(plant_map: PlantMap, net_file: Path = NET_FILE, bays: list[Polygon] | None = None):
    """(asphalt, white centre lines, yellow slot and bay outlines) as shapely geometry in plant metres."""
    import sumolib
    net = sumolib.net.readNet(str(net_file), withInternal=True)
    lanes = [LineString(lane.getShape()).buffer(lane.getWidth() / 2, cap_style="flat")
             for edge in net.getEdges(withInternal=True) for lane in edge.getLanes()
             if len(lane.getShape()) >= 2]
    junctions = [Polygon(node.getShape()) for node in net.getNodes() if len(node.getShape()) >= 3]
    asphalt = unary_union([g for g in lanes + junctions if g.is_valid and not g.is_empty])

    # centre line on two-way roads: sumolib gives each direction's lane shape, so offset one of them
    lines, done = [], set()
    for edge in net.getEdges():
        back = [e for e in edge.getToNode().getOutgoing() if e.getToNode() == edge.getFromNode()]
        if not back or any(e.getID() in done for e in back):
            continue
        done.add(edge.getID())
        lane = edge.getLanes()[0]
        line = LineString(lane.getShape()).offset_curve(lane.getWidth() / 2)
        trimmed = line.difference(unary_union(junctions).buffer(0.5)) if junctions else line
        lines.append(trimmed.buffer(LINE_M / 2, cap_style="flat"))

    slots = []
    for name, p in plant_map.points.items():
        if off_road(name):
            nb = plant_map.points[next(iter(plant_map.edges[name]))]
            yaw = math.atan2(p.y - nb.y, p.x - nb.x)
            slots.append(_outline(p.x, p.y, yaw, SLOT_LENGTH_M, SLOT_WIDTH_M))
    bay_lines = [_outline_poly(b) for b in (bays or [])] + slots
    return asphalt, unary_union(lines) if lines else Polygon(), \
        unary_union(bay_lines) if bay_lines else Polygon()


def _outline(x: float, y: float, yaw: float, length: float, width: float):
    c, s = math.cos(yaw), math.sin(yaw)
    corners = [(x + c * dx - s * dy, y + s * dx + c * dy)
               for dx, dy in ((length / 2, width / 2), (-length / 2, width / 2),
                              (-length / 2, -width / 2), (length / 2, -width / 2))]
    return _outline_poly(Polygon(corners))


def _outline_poly(poly: Polygon):
    return poly.exterior.buffer(LINE_M / 2)


def _geom_node(name: str, geom, colour, z: float, offset: tuple[float, float]):
    from panda3d.core import Geom, GeomNode, GeomTriangles, GeomVertexData, GeomVertexFormat, GeomVertexWriter

    data = GeomVertexData(name, GeomVertexFormat.get_v3n3c4(), Geom.UH_static)
    vertex, normal, color = (GeomVertexWriter(data, c) for c in ("vertex", "normal", "color"))
    tris = GeomTriangles(Geom.UH_static)
    n = 0
    for poly in _polygons(geom):
        for tri in _polygons(shapely.constrained_delaunay_triangles(poly)):
            pts = list(tri.exterior.coords)[:3]
            # counter-clockwise seen from above, so the face points up
            if (pts[1][0] - pts[0][0]) * (pts[2][1] - pts[0][1]) - (pts[1][1] - pts[0][1]) * (pts[2][0] - pts[0][0]) < 0:
                pts = [pts[0], pts[2], pts[1]]
            for x, y in pts:
                vertex.add_data3(x - offset[0], y - offset[1], z)
                normal.add_data3(0, 0, 1)
                color.add_data4(*colour)
            tris.add_vertices(n, n + 1, n + 2)
            n += 3
    geom_ = Geom(data)
    geom_.add_primitive(tris)
    node = GeomNode(name)
    node.add_geom(geom_)
    return node


def draw(engine, plant_map: PlantMap, centre: tuple[float, float], bays: list[Polygon] | None = None):
    """Attach the plant surface under MetaDrive's world node. Returns the node path."""
    asphalt, paint, bay_paint = surfaces(plant_map, bays=bays)
    root = engine.worldNP.attach_new_node("marshal-plant")
    for name, geom, colour, z in (("asphalt", asphalt, ASPHALT, 0.02), ("paint", paint, PAINT, 0.04),
                                  ("bays", bay_paint, BAY_PAINT, 0.04)):
        root.attach_new_node(_geom_node(name, geom, colour, z, centre))
    root.set_two_sided(True)
    return root
