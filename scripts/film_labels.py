"""Labels burned into the filmed replays: which run this is, and where in the plant the car is.

Town05 is a city map, so a replay needs telling where the car is: "Inspection gate", "Rework bay -
electrical", "Yard slot Y3". The zone comes from the nearest named plant point to the shot's middle
(plant_map.yaml, CARLA coordinates). Drawn with Pillow; without it the film is written unlabelled.
"""

from __future__ import annotations

import math

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:                       # the film still works, just without labels
    Image = None

# what each recording shows, by the run script that made it
RUN_TITLES = {
    "yard": "Flow 1 · Yard traffic", "day5": "Flow 2 · A car stuck at a box", "day2": "Flow 3 · Gate and lighting",
    "day4": "Flows 4-5 · Lamp check and rework", "scene1": "Demo scene 1 · One car, line to yard",
    "plant": "Several cars through the plant", "lab": "Flows 6-7 · Test lab", "release": "Scenes 4-5 · Release gate",
}


def point_label(name: str) -> str:
    if name.startswith("rework."):
        return f"Rework bay · {name.split('.', 1)[1]}"
    if name.startswith("yard.Y"):
        return f"Yard · slot {name.split('.', 1)[1]}"
    if name.startswith("finishing_corner"):
        return "Finishing corner"
    return {"line_end": "Line end", "inspection_gate": "Inspection gate", "test_track": "Test track",
            "report_point": "Report point", "finishing": "Finishing", "lane_c": "Lane C",
            "buffer_zone_b": "Buffer zone B", "production_control": "Production control",
            "yard.entry": "Yard entry", "loading_bay": "Loading bay"}.get(name, name.replace("_", " ").capitalize())


def plant_points() -> dict[str, tuple[float, float]]:
    """Named plant points with their CARLA x, y (empty if the plant map cannot be loaded)."""
    try:
        from marshal_ai.plant import load_plant_map
    except ImportError:
        return {}
    m = load_plant_map()
    out = {}
    for name, p in m.points.items():
        c = getattr(p, "carla", None)
        if c is not None:
            out[name] = (c.x, c.y) if hasattr(c, "x") else (c["x"], c["y"])
    return out


# Places a car stops at: named only when the shot ends there. Driving past a bay is not being in it.
STOPS = ("rework.", "production_control", "yard.Y", "buffer_zone_b")


def zone_for(points: list[tuple[float, float, float]], named: dict[str, tuple[float, float]]) -> str | None:
    """Where the car is in this shot: the bay or slot it stops at, else the plant zone it drives through."""
    if not points or not named:
        return None
    end, mid = points[-1], points[len(points) // 2]
    stops = {n: xy for n, xy in named.items() if n.startswith(STOPS)}
    # Stopped, not just passing: long drives are split into shots, and a split can fall beside a slot.
    stopped = math.dist(end[:2], points[-min(len(points), 10)][:2]) < 0.5
    if stops and stopped:
        stop = min(stops, key=lambda n: math.dist(stops[n], end[:2]))
        # on the mark: a car parked in a slot or bay is pulled onto it (within about a metre), while
        # one pausing in the lane beside a slot on its way further is 3.3 m off it
        if math.dist(stops[stop], end[:2]) < 2.0:
            return point_label(stop)
    road = {n: xy for n, xy in named.items() if not n.startswith(STOPS) and n != "lane_c"}
    name = min(road, key=lambda n: math.dist(road[n], mid[:2]))
    return point_label(name) if math.dist(road[name], mid[:2]) < 20 else "Plant road"


def run_title(recording: str) -> str:
    prefix = recording.split("-", 1)[0]
    return RUN_TITLES.get(prefix, prefix)


class Labeller:
    def __init__(self, width: int, height: int):
        self.ok = Image is not None
        self.w, self.h = width, height
        if self.ok:
            self.big = ImageFont.load_default(size=max(18, height // 22))
            self.small = ImageFont.load_default(size=max(14, height // 36))

    def draw(self, raw: bytes, title: str, place: str | None, vin: str | None) -> bytes:
        if not self.ok:
            return raw
        img = Image.frombuffer("RGBA", (self.w, self.h), raw, "raw", "BGRA", 0, 1).copy()
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        pad = self.h // 40
        # top left: the product and the run
        head = f"MARSHAL  ·  {title}"
        tw = d.textlength(head, font=self.small)
        d.rectangle([0, 0, tw + 2 * pad, pad * 2 + self.small.size], fill=(12, 16, 20, 170))
        d.text((pad, pad), head, font=self.small, fill=(255, 255, 255, 255))
        # lower third: where the car is, and which car
        if place:
            line = place.upper()
            sub = vin or ""
            lw = max(d.textlength(line, font=self.big), d.textlength(sub, font=self.small))
            top = self.h - pad * 4 - self.big.size - self.small.size
            d.rectangle([pad, top, pad * 3 + lw, self.h - pad], fill=(12, 16, 20, 175))
            d.rectangle([pad, top, pad + 6, self.h - pad], fill=(240, 170, 60, 255))
            d.text((pad * 2 + 4, top + pad), line, font=self.big, fill=(255, 255, 255, 255))
            if sub:
                d.text((pad * 2 + 4, top + pad + self.big.size + pad // 2), sub, font=self.small,
                       fill=(210, 215, 220, 255))
        out = Image.alpha_composite(img, layer)
        return out.tobytes("raw", "BGRA")
