"""Route choice eval: does Gemini pick the right way across the plant?

    python -m marshal_ai.eval.routes --runs 3

Situations on the real plant map, from the report point to a yard slot (through the finishing
corner, 110 m, or along lane C, 123 m), each with the way a careful supervisor would send the car.
They are built through the same code the yard uses (flows/route.py), on an offline runtime, and only
the route.choose call goes to Gemini.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass, field

CORNER, LANE_C = "via finishing_corner", "via lane_c"


@dataclass(frozen=True)
class Situation:
    name: str
    expect: str
    hazards: dict = field(default_factory=dict)
    stopped_at: str | None = None          # a car waiting for help here
    driving: tuple = ()                    # (vin, way) other cars already driving a way


SITUATIONS = [
    Situation("forklift in the corner", LANE_C, {"finishing_corner": "forklift unloading pallets across the road"}),
    Situation("forklift parked away from the road", CORNER,
              {"finishing_corner": "a forklift parked and switched off on the hard standing, 10 m from the road"}),
    Situation("painting finished", CORNER, {"finishing_corner": "line painting, finished yesterday, paint dry"}),
    Situation("car stopped in the corner", LANE_C, stopped_at="finishing_corner.p2"),
    Situation("car stopped in lane C", CORNER, stopped_at="lane_c"),
    Situation("people crossing lane C", CORNER, {"lane_c": "two people crossing on foot with a trolley"}),
    Situation("people where both ways meet", CORNER, {"finishing": "people walking along the finishing lane"}),
    Situation("two cars driving the corner", CORNER, driving=(("VIN-301", "corner"), ("VIN-302", "corner"))),
    Situation("forklift in the corner, a car on lane C", LANE_C,
              {"finishing_corner": "forklift reversing out of the racking"}, driving=(("VIN-301", "lane_c"),)),
    Situation("worker at the corner's blind spot", LANE_C,
              {"finishing_corner.p2": "a worker sweeping at the blind spot of the corner"}),
]


async def run_one(situation: Situation, llm) -> dict:
    from marshal_ai.app import MarshalApp
    from marshal_ai.config import Settings
    from marshal_ai.demo import seed_demo
    from marshal_ai.flows import route
    from marshal_ai.records import AssistanceCase
    from marshal_ai.runtime import build_runtime

    rt = build_runtime(Settings(bus="memory", store="memory", driving="fake", llm="offline"), llm=llm)
    app = MarshalApp(rt)
    seed_demo(rt, ["VIN-214"])
    rt.plant.hazards.update(situation.hazards)
    if situation.stopped_at:
        rt.store.put(AssistanceCase(case_id="A-1", vin="VIN-299", location=situation.stopped_at,
                                    options=["wait_for_removal"]))
    ways = rt.map.routes("report_point", "yard.Y4")
    in_use = {vin: (ways[0] if way == "corner" else ways[1]) for vin, way in situation.driving}
    started = time.monotonic()
    _points, record = await route.choose(rt, "VIN-214", "report_point", "yard.Y4", in_use, 10.0, "always")
    return {"situation": situation.name, "expect": situation.expect, "choice": record["choice"],
            "p": record.get("probs", {}).get(record["choice"]), "by": record["by"],
            "seconds": round(time.monotonic() - started, 2)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--policy", choices=["gemini", "offline"], default="gemini")
    ap.add_argument("--film", default=None, help="instead: film four situations to this .mp4")
    args = ap.parse_args(argv)
    if args.policy == "gemini":
        from marshal_ai.config import settings
        from marshal_ai.llm import GeminiLLM, RoutedLLM
        from marshal_ai.offline import offline_llm
        llm = RoutedLLM(offline_llm(), {"route.": GeminiLLM(settings.model)})
    else:
        from marshal_ai.offline import offline_llm
        llm = offline_llm()
    if args.film:
        from pathlib import Path
        Path(args.film).parent.mkdir(parents=True, exist_ok=True)
        print(film(args.film, llm))
        return
    results = []
    for _ in range(args.runs):
        for s in SITUATIONS:
            r = asyncio.run(run_one(s, llm))
            results.append(r)
            mark = "ok " if r["choice"] == r["expect"] else "XX "
            print(f"{mark}{r['situation']:40s} {r['choice']:22s} p={r['p']} {r['seconds']} s by {r['by']}", flush=True)
    right = sum(r["choice"] == r["expect"] for r in results)
    gem = [r["seconds"] for r in results if r["by"] == "gemini"]
    print(f"== route choice, {args.policy}: {right}/{len(results)} right"
          + (f", median {statistics.median(gem):.2f} s, slowest {max(gem):.2f} s" if gem else ""))



# ---------- the film: python -m marshal_ai.eval.routes --film out/routes.mp4 ----------

FILM_SCENES = [
    ("Nothing reported", Situation("quiet", CORNER)),
    ("Forklift unloading in the corner", SITUATIONS[0]),
    ("A car stopped in lane C, waiting for help", SITUATIONS[4]),
    ("Line painting in the corner, finished yesterday", SITUATIONS[2]),
]
W, H = 1280, 720
BG, ROAD, TEXT, DIM = (18, 23, 29), (70, 78, 90), (233, 230, 222), (140, 149, 161)
AMBER, BLUE, PINK, ORANGE = (227, 155, 46), (127, 167, 255), (255, 110, 200), (232, 120, 60)


async def decide_scene(situation: Situation, llm, ask_when: str) -> tuple[dict, list[list[str]], dict]:
    from marshal_ai.app import MarshalApp
    from marshal_ai.config import Settings
    from marshal_ai.demo import seed_demo
    from marshal_ai.flows import route
    from marshal_ai.records import AssistanceCase
    from marshal_ai.runtime import build_runtime

    rt = build_runtime(Settings(bus="memory", store="memory", driving="fake", llm="offline"), llm=llm)
    MarshalApp(rt)
    seed_demo(rt, ["VIN-214"])
    rt.plant.hazards.update(situation.hazards)
    if situation.stopped_at:
        rt.store.put(AssistanceCase(case_id="A-1", vin="VIN-P4", location=situation.stopped_at,
                                    options=["wait_for_removal"]))
    ways = rt.map.routes("report_point", "yard.Y4")
    started = time.monotonic()
    points, record = await route.choose(rt, "VIN-P1", "report_point", "yard.Y4", {}, 10.0, ask_when)
    record["seconds"] = round(time.monotonic() - started, 2)
    return record, ways, {"map": rt.map, "points": ["report_point", *points]}


def _name(label: str) -> str:
    return {"via lane_c": "via lane C", "via finishing_corner": "via the finishing corner"}.get(label, label)


def film(out, llm) -> str:
    import imageio_ffmpeg
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    def font(size, bold=False):
        for name in (("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
                     "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else
                     "/System/Library/Fonts/Supplemental/Arial.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    f_title, f_big, f_mid, f_small = font(34, True), font(24, True), font(20), font(16)
    scenes = []
    for title, situation in FILM_SCENES:
        record, ways, extra = asyncio.run(decide_scene(situation, llm, "reason"))
        scenes.append((title, situation, record, ways, extra))
        print(f"{title}: {record['choice']} by {record['by']} {record.get('probs', '')}", flush=True)
    m = scenes[0][4]["map"]

    def px(name):
        p = m.points[name]
        return (70 + p.x * 4.75, 110 + (50 - p.y) * 4.6)

    def base(d, situation, ways, chosen=None):
        for a in m.edges:
            for b in m.edges[a]:
                if a < b:
                    d.line([px(a), px(b)], fill=ROAD, width=10)
        zones = {}
        for p in m.points.values():
            zones.setdefault(p.zone, []).append(px(p.name))
        for z, pts in zones.items():
            if z in ("finishing", "test_track"):
                continue
            cx = sum(x for x, _ in pts) / len(pts)
            cy = min(y for _, y in pts) - 26
            d.text((cx, cy), z.replace("_", " "), fill=DIM, font=f_small, anchor="mm")
        for name, label, dx, dy in (("lane_c", "lane C", -14, 40), ("report_point", "report point", 0, 24),
                                    ("finishing", "finishing", 0, -22)):
            x, y = px(name)
            d.text((x + dx, y + dy), label, fill=DIM, font=f_small, anchor="rm" if dx < 0 else "mm")
        for i, way in enumerate(ways):
            colour = BLUE if "lane_c" in way else PINK
            if chosen is not None and way != chosen:
                colour = tuple(c // 3 for c in colour)
            d.line([px(p) for p in way], fill=colour, width=6 if chosen is None or way == chosen else 4)
        for s in ("yard.Y1", "yard.Y2", "yard.Y3", "yard.Y4", "yard.Y5", "yard.Y6", "yard.Y7", "yard.Y8"):
            x, y = px(s)
            d.rectangle([x - 12, y - 7, x + 12, y + 7], outline=AMBER if s == "yard.Y4" else DIM, width=2)
        x, y = px("yard.Y4")
        d.text((x - 30, y), "Y4", fill=AMBER, font=f_small, anchor="rm")
        for place, what in situation.hazards.items():
            pts = [px(p.name) for p in m.points.values() if p.zone == place or p.name == place]
            cx, cy = sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)
            d.polygon([(cx + 40, cy - 34), (cx + 58, cy - 4), (cx + 22, cy - 4)], fill=AMBER)
            d.text((cx + 40, cy - 14), "!", fill=BG, font=f_big, anchor="mm")
        if situation.stopped_at:
            x, y = px(situation.stopped_at)
            d.rectangle([x - 22, y - 12, x + 22, y + 12], fill=ORANGE)
            d.text((x, y), "P4", fill=BG, font=f_small, anchor="mm")
            d.text((x, y - 26), "stopped, waiting for help", fill=ORANGE, font=f_small, anchor="mm")

    def car(d, xy, label="P1"):
        x, y = xy
        d.rounded_rectangle([x - 22, y - 12, x + 22, y + 12], radius=5, fill=(233, 230, 222), outline=BG, width=2)
        d.text((x, y), label, fill=BG, font=f_small, anchor="mm")

    def panel(d, title, lines):
        d.rectangle([0, 0, W, 64], fill=(27, 34, 43))
        d.text((24, 32), "MARSHAL · route choice", fill=AMBER, font=f_mid, anchor="lm")
        d.text((W - 24, 32), title, fill=TEXT, font=f_big, anchor="rm")
        d.rectangle([0, 560, W, H], fill=(27, 34, 43))
        for i, (colour, text) in enumerate(lines):
            d.text((24, 584 + i * 34), text, fill=colour, font=f_mid)

    def along(points, t):
        xs = [px(p) for p in points]
        lens = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(xs, xs[1:])]
        goal, acc = t * sum(lens), 0.0
        for (a, b), l in zip(zip(xs, xs[1:]), lens):
            if acc + l >= goal:
                k = (goal - acc) / l if l else 0
                return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
            acc += l
        return xs[-1]

    fps = 25
    writer = imageio_ffmpeg.write_frames(str(out), (W, H), fps=fps, codec="libx264", pix_fmt_out="yuv420p", quality=8)
    writer.send(None)

    def send(img, seconds):
        data = np.asarray(img.convert("RGB")).tobytes()
        for _ in range(int(seconds * fps)):
            writer.send(data)

    for n, (title, situation, record, ways, extra) in enumerate(scenes, 1):
        reported = ([f"{k.replace('_', ' ')}: {v}" for k, v in situation.hazards.items()]
                    + ([f"VIN-P4 stopped at {situation.stopped_at.replace('_', ' ')}"] if situation.stopped_at else []))
        facts = []
        for w in record["ways"]:
            colour = BLUE if "lane_c" in w["label"] else PINK
            what = "; ".join(w["hazards"] + w["stopped"]) or "nothing reported"
            facts.append((colour, f"{_name(w['label'])}: {w['length_m']} m · {what}"))
        # 1. what is going on, both ways
        img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img)
        base(d, situation, ways); car(d, px("report_point"))
        panel(d, f"{n}/4  {title}", [(TEXT, "VIN-P1 passed and is going to yard slot Y4. Two ways:")] + facts)
        send(img, 3.0)
        # 2. the decision
        chosen = next(w for w in ways if w[1:] == extra["points"][1:])
        if record["by"] == "gemini":
            p = record["probs"][record["choice"]]
            verdict = (BLUE, f"GEMINI chose {_name(record['choice'])} ({p:.2f}) in {record['seconds']:.1f} s")
            sub = (DIM, "one token, a probability for each way; the yard rules check it and the car drives it")
        else:
            verdict = (AMBER, f"YARD RULES: {_name(record['choice'])}, the shortest way")
            sub = (DIM, "nothing reported on either way, so no question for Gemini")
        img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img)
        base(d, situation, ways, chosen); car(d, px("report_point"))
        panel(d, f"{n}/4  {title}", [verdict, sub] + facts[:1] + facts[1:])
        send(img, 2.5)
        # 3. it drives there
        frames = int(4.0 * fps)
        for i in range(frames + 1):
            img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img)
            base(d, situation, ways, chosen); car(d, along(extra["points"], i / frames))
            panel(d, f"{n}/4  {title}", [verdict, sub] + facts)
            send(img, 1 / fps)
        send(img, 1.2)
    writer.close()
    return str(out)


if __name__ == "__main__":
    main()
