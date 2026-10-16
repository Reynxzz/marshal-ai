"""Closed-loop parking eval: Gemini (or the planner) parks a kinematic car, one decision at a time.

    python -m marshal_ai.eval.parking --policy gemini
    python -m marshal_ai.eval.parking --policy planner --frames out/park

The same ParkingSession the CARLA backend uses, driving a bicycle-model car instead of CARLA's, so
it runs without the VM. Each case starts the car where the CARLA backend hands over to parking -
in the lane beside the yard, a run-up before the slot - with the slots on either side empty or
taken. What it measures is the decision loop: does Gemini get the car inside the slot's limits,
in how many moves, how long each decision takes, and how often the safety check had to stop it.
It does not measure CARLA's tyres and steering lag; that needs the VM.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from marshal_ai.driving.parking import (
    CAR_LENGTH_M, CAR_WIDTH_M, SLOT_LENGTH_M, Box, Command, ParkingSession, Pose, kerb, planned_move,
    render, touching,
)
from marshal_ai.llm import Decision, decision_schema
from marshal_ai.driving.parking import OPTIONS

DT = 0.05
ACCEL = 1.5                         # m/s^2, both ways


@dataclass(frozen=True)
class Case:
    name: str
    neighbours: str                 # none | behind | ahead | both
    x0: float
    y0: float = 3.5                 # the lane beside the shoulder
    yaw0_deg: float = 0.0


def cases() -> list[Case]:
    out = []
    for nb in ("none", "behind", "ahead", "both"):
        out.append(Case(f"{nb}-run12", nb, -12.0))
        out.append(Case(f"{nb}-run8", nb, -8.0))
        out.append(Case(f"{nb}-run10-skew", nb, -10.0, 3.0, 6.0))
    return out


def obstacles_for(case: Case) -> list[Box]:
    boxes = [kerb()]
    if case.neighbours in ("behind", "both"):
        boxes.append(Box(-SLOT_LENGTH_M, 0.0, 0.0, CAR_LENGTH_M, CAR_WIDTH_M, "the car in the slot behind"))
    if case.neighbours in ("ahead", "both"):
        boxes.append(Box(SLOT_LENGTH_M, 0.0, 0.0, CAR_LENGTH_M, CAR_WIDTH_M, "the car in the slot ahead"))
    return boxes


class KinematicCar:
    def __init__(self, pose: Pose):
        self.pose = pose
        self.speed = 0.0

    def step(self, cmd: Command, dt: float) -> None:
        from marshal_ai.driving.parking import advance

        target = 0.0 if cmd.brake else cmd.speed
        delta = max(-ACCEL * dt, min(ACCEL * dt, target - self.speed))
        self.speed += delta
        if abs(self.speed) > 1e-6:
            direction = 1 if self.speed > 0 else -1
            self.pose = advance(self.pose, direction, cmd.wheel, abs(self.speed) * dt, step=0.02)


def planner_decide(view, image=None) -> Decision:
    """The planner's own choice: its shortest path, or its next move."""
    options = view.options
    if view.mode == "paths":
        choice = options[0]                  # path_A, or parked, or ask_a_person when there is none
    else:
        choice = planned_move(view.pose, view.obstacles)
    probs = {o: (1.0 if o == choice else 0.0) for o in options}
    return decision_schema(*options)(choice=choice, probs=probs)


def run_case(case: Case, decide, frames: Path | None = None, max_sim_s: float = 600.0,
             mode: str = "paths", on_tick=None) -> dict:
    obstacles = obstacles_for(case)
    car = KinematicCar(Pose(case.x0, case.y0, math.radians(case.yaw0_deg)))
    session = ParkingSession("VIN-EVAL", "Y4", mode=mode)
    latencies, plans, collided, t = [], [], None, 0.0
    while not session.done and t < max_sim_s:
        if session.needs_decision:
            planned = time.monotonic()
            view = session.view(car.pose, obstacles)
            plans.append(time.monotonic() - planned)
            if session.done:                         # no path in: a person decides
                break
            image = render(view)
            if frames is not None:
                frames.mkdir(parents=True, exist_ok=True)
                (frames / f"{case.name}-{session.decisions:02d}.png").write_bytes(image)
            started = time.monotonic()
            try:
                decision = decide(view, image)
            except Exception as exc:                 # a failed call ends the session, as in CARLA
                session.note = f"decision failed: {exc}"
                session.finish("error")
                break
            latency = time.monotonic() - started
            latencies.append(latency)
            session.apply(decision, car.pose, latency, view)
            continue
        if session.done:
            break
        cmd = session.control(car.pose, obstacles, car.speed, DT)
        car.step(cmd, DT)
        t += DT
        if on_tick is not None:
            on_tick(t, car.pose, view, session)
        hit = touching(car.pose, obstacles)
        if hit is not None and collided is None:
            collided = hit.label
    if frames is not None:
        view = session.view(car.pose, obstacles)
        (frames / f"{case.name}-final.png").write_bytes(render(view))
    p = car.pose
    return {
        "case": case.name, "neighbours": case.neighbours, "outcome": session.outcome or "timeout",
        "decisions": session.decisions, "safety_stops": session.safety_stops, "refused": session.refused,
        "collided": collided, "final": {"along_m": round(p.x, 2), "side_m": round(p.y, 2),
                                        "yaw_deg": round(p.yaw_deg, 1)},
        "drive_s": round(t, 1), "latency_s": [round(x, 2) for x in latencies],
        "plan_s": [round(x, 2) for x in plans], "trace": session.trace,
    }


def film(case: Case, trace: list[dict], out: Path, fps: int = 20, speed: float = 2.0) -> Path:
    """Replay recorded decisions on the kinematic car as a bird's-eye video (needs imageio-ffmpeg).

    The planner is deterministic, so offering the same paths again and taking the recorded choice
    reproduces the run. Each frame shows the paths offered at the last decision and Gemini's choice.
    """
    import io
    import imageio_ffmpeg
    import numpy as np
    from PIL import Image, ImageDraw

    choices = [step["choice"] for step in trace]
    probs = [step["p"] for step in trace]
    shown: list[str] = []

    def replay(view, image=None):
        from marshal_ai.driving.parking import Path as Route, _trace

        i = len(shown)
        step = trace[i] if i < len(trace) else {}
        choice = choices[i] if i < len(choices) else "ask_a_person"
        what = choice.replace("_", " ") + (f", {step['strategy']}" if step.get("strategy") else "")
        shown.append(f"GEMINI chose {what} ({probs[i] if i < len(probs) else 0:.2f})")
        if choice.startswith("path_") and step.get("path"):
            # drive exactly the recorded path, whatever this replay's planner happens to propose
            moves = tuple(step["path"])
            view.paths = [p for p in view.paths if p.option != choice] + [
                Route(choice[-1], moves, tuple(_trace(view.pose, moves)), 0.0, 0, 1.0, "", step.get("strategy", ""))]
        options = view.options
        if choice not in options:
            choice = options[-1]
        return decision_schema(*options)(choice=choice, probs={o: float(o == choice) for o in options})

    out.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio_ffmpeg.write_frames(str(out), (640, 400), fps=fps, codec="libx264",
                                         pix_fmt_out="yuv420p", quality=7)
    writer.send(None)
    every = max(1, int(round(1 / (DT * fps) * speed)))
    count = [0]

    def frame(t, pose, view, session):
        count[0] += 1
        if count[0] % every:
            return
        from dataclasses import replace
        still = Image.open(io.BytesIO(render(replace(view, pose=pose)))).convert("RGB")
        d = ImageDraw.Draw(still)
        d.rectangle([8, 352, 632, 392], fill=(18, 23, 29))
        d.text((16, 358), shown[-1] if shown else "", fill=(127, 167, 255))
        d.text((16, 374), f"decision {session.decisions}  ·  {t:5.1f} s driven  ·  {case.name}", fill=(220, 220, 220))
        writer.send(np.asarray(still).tobytes())

    from marshal_ai.driving import parking as _parking
    budget, _parking.PLAN_BUDGET_S = _parking.PLAN_BUDGET_S, 60.0   # never cut short on the replay
    try:
        result = run_case(case, replay, on_tick=frame)
    finally:
        _parking.PLAN_BUDGET_S = budget
    for _ in range(fps * 2):                             # hold the last frame
        writer.send(np.asarray(Image.open(io.BytesIO(render(result_view(case, result)))).convert("RGB")).tobytes())
    writer.close()
    return out


def result_view(case: Case, result: dict):
    f = result["final"]
    pose = Pose(f["along_m"], f["side_m"], math.radians(f["yaw_deg"]))
    return ParkingSession("VIN-EVAL", "Y4").view(pose, obstacles_for(case))


def summarise(results: list[dict]) -> str:
    lines = []
    for nb in ("none", "behind", "ahead", "both", "all"):
        rs = [r for r in results if nb == "all" or r["neighbours"] == nb]
        if not rs:
            continue
        parked = [r for r in rs if r["outcome"] == "parked"]
        lat = [x for r in rs for x in r["latency_s"]]
        plan = [x for r in rs for x in r.get("plan_s", [])]
        lines.append(
            f"  {nb:7s} parked {len(parked)}/{len(rs)}"
            f"  collisions {sum(1 for r in rs if r['collided'])}"
            f"  decisions median {statistics.median([r['decisions'] for r in parked]) if parked else '-'}"
            f"  safety stops {sum(r['safety_stops'] for r in rs)}"
            + (f"  decision median {statistics.median(lat):.2f} s" if lat else "")
            + (f"  planning median {statistics.median(plan):.2f} s" if plan else ""))
    for r in results:
        f = r["final"]
        lines.append(f"    {r['case']:18s} {r['outcome']:8s} decisions {r['decisions']:2d}  "
                     f"final {f['along_m']:+.2f} m / {f['side_m']:+.2f} m / {f['yaw_deg']:+.1f} deg"
                     + (f"  HIT {r['collided']}" if r["collided"] else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--policy", choices=["gemini", "planner"], default="gemini")
    ap.add_argument("--model", default=None)
    ap.add_argument("--only", default="", help="comma list of case names")
    ap.add_argument("--mode", choices=["paths", "moves"], default="paths")
    ap.add_argument("--frames", type=Path, default=None, help="save each decision's view here")
    ap.add_argument("--out", type=Path, default=None, help="write all results as JSON")
    ap.add_argument("--film", type=Path, default=None,
                    help="with --from: replay each case's recorded decisions into <dir>/<case>.mp4")
    ap.add_argument("--from", dest="source", type=Path, default=None, help="results JSON to film")
    args = ap.parse_args(argv)

    if args.film is not None:
        recorded = json.loads(args.source.read_text())
        by_name = {c.name: c for c in cases()}
        for r in recorded:
            if args.only and r["case"] not in args.only.split(","):
                continue
            print(film(by_name[r["case"]], r["trace"], args.film / f"{r['case']}.mp4"))
        return
    if args.policy == "gemini":
        from marshal_ai.config import settings
        from marshal_ai.driving.parking import GeminiParker
        from marshal_ai.llm import GeminiLLM
        decide = GeminiParker(GeminiLLM(args.model or settings.model), args.mode).decide
    else:
        decide = planner_decide
    chosen = [c for c in cases() if not args.only or c.name in args.only.split(",")]
    results = []
    for case in chosen:
        r = run_case(case, decide, args.frames, mode=args.mode)
        results.append(r)
        print(f"{case.name}: {r['outcome']} in {r['decisions']} moves", flush=True)
    print(f"== parking {args.policy}, {args.mode}")
    print(summarise(results))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
