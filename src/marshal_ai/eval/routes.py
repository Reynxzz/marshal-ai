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
    args = ap.parse_args(argv)
    if args.policy == "gemini":
        from marshal_ai.config import settings
        from marshal_ai.llm import GeminiLLM, RoutedLLM
        from marshal_ai.offline import offline_llm
        llm = RoutedLLM(offline_llm(), {"route.": GeminiLLM(settings.model)})
    else:
        from marshal_ai.offline import offline_llm
        llm = offline_llm()
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


if __name__ == "__main__":
    main()
