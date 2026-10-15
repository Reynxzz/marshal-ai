"""Gemini parks the car, as a decision model, behind a safety check.

Two modes (MARSHAL_PARK_MODE):

  paths (default)  A planner proposes up to three paths into the slot, each drawn on the bird's-eye
                   view with its length, its changes of direction and how close it comes to anything.
                   Gemini chooses the path - or parked, or ask a person - and the car drives the first
                   segment of it (up to the next change of direction, at most three moves). Then it
                   stops, the planner proposes again from where the car is, and Gemini chooses again.
                   On the parking eval (12 starts, gemini-2.5-flash, asia-southeast1, 2026-10-14):
                   8 of the 9 slots a car can get into parked, 5-7 decisions each, 1.1 s a decision,
                   no collision; the 9th and the 3 slots between two parked cars (5.5 m apart, no
                   way in for a 4.9 m car) went to a person.
  moves            Gemini chooses each 1 m manoeuvre itself (described below). Measured on the parking
                   eval it did not park a car: without thinking it turned toward the slot and back for
                   40 moves; with guide lines showing where each move would end, the same; with
                   thinking it asked for a person at once, or without that option drove off. Kept for
                   experiments.

The rest of this note describes the moves mode, whose pieces the paths mode reuses.

Until now the last metres into a yard slot were the stand-in's: it steered at the slot by hand and
then set the car on it (carla_backend._square_up). Here Gemini drives that part. It is asked as a
decision model (llm.Decision): one manoeuvre from a closed list - forward or reverse with the wheel
left, straight or right, a short nudge, "parked", or "ask a person" - with a probability for each
from the answer token's log-probability. Each manoeuvre is about a metre at walking pace; then the
car stops, Gemini sees where it is, and chooses again.

What Gemini sees: a bird's-eye view of the slot drawn from the simulator's poses (the target slot,
the car, the cars around it, the kerb), as a parking system's surround view would give it, and the
same thing measured: how far short of or past the slot, how far to the side, the heading error and
the free space in front and behind.

What Gemini cannot do: touch anything. Every manoeuvre is checked here before and while it runs. A
move whose next 0.4 m would bring the car within 0.2 m of another car or the kerb is stopped, and
Gemini is told why. "Parked" is accepted only when the car is inside the slot's limits. Speed is
fixed at walking pace. After MAX_DECISIONS without parking, or if Gemini asks for a person, the
session ends and the caller decides what happens (the CARLA backend falls back to the stand-in).

Everything here is plain geometry in the slot's frame - x along the slot (the way a parked car
faces), y to its left, angles counter-clockwise - so the same session runs against CARLA and against
the kinematic car the parking eval uses (marshal_ai.eval.parking).
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from functools import cached_property, lru_cache

CAR_LENGTH_M = 4.9                  # vehicle.lincoln.mkz_2020, the plant's car
CAR_WIDTH_M = 1.9
WHEELBASE_M = 2.85
STEER_DEG = 30.0                    # front wheel angle for "wheel left/right"
SLOT_LENGTH_M = 5.5                 # yard slots are 5.5 m apart along the shoulder
SLOT_WIDTH_M = 3.5                  # the shoulder they are on

MOVE_M = 1.0
NUDGE_M = 0.3
FORWARD_MPS = 0.7                   # about 2.5 km/h
REVERSE_MPS = 0.6
MAX_MOVE_S = 6.0                    # a move that has not covered its distance by then is over

# Parked: the whole car inside the slot's lines, nose within PARKED_YAW_DEG of the slot's direction.
# A 4.9 m car in a 5.5 m slot leaves 0.3 m at each end, so being inside the lines is the precision
# that matters: a car that is, is clear of the lane and of the cars in the slots either side.
PARKED_YAW_DEG = 8.0
# ...and clear of the lines by this much: inside them at all left the first car parked by Gemini on
# CARLA against the lane's edge, 0.76 m off the slot's middle, where on film it looked as if it stood
# in the road (the slot's only visible line is the outer one)
PARKED_INSET_SIDE_M = 0.3
PARKED_INSET_END_M = 0.0              # 4.9 m in 5.5 m: the ends have no slack to spare

SAFETY_LOOKAHEAD_M = 0.4
SAFETY_MARGIN_M = 0.2
MAX_DECISIONS = 40
MAX_REFUSED = 3                     # "parked" answers outside the limits before the session gives up

# id -> (direction, wheel: +1 left / -1 right, distance)
MOVES = {
    "forward_left": (1, 1, MOVE_M),
    "forward_straight": (1, 0, MOVE_M),
    "forward_right": (1, -1, MOVE_M),
    "reverse_left": (-1, 1, MOVE_M),
    "reverse_straight": (-1, 0, MOVE_M),
    "reverse_right": (-1, -1, MOVE_M),
    "nudge_forward": (1, 0, NUDGE_M),
    "nudge_back": (-1, 0, NUDGE_M),
}
OPTIONS = tuple(MOVES) + ("parked", "ask_a_person")

OPTION_TEXT = {
    "forward_left": "drive forward 1 m, wheel left",
    "forward_straight": "drive forward 1 m, wheel straight",
    "forward_right": "drive forward 1 m, wheel right",
    "reverse_left": "reverse 1 m, wheel left",
    "reverse_straight": "reverse 1 m, wheel straight",
    "reverse_right": "reverse 1 m, wheel right",
    "nudge_forward": "nudge forward 0.3 m, wheel straight",
    "nudge_back": "nudge back 0.3 m, wheel straight",
    "parked": "stop here: the car is parked",
    "ask_a_person": "stop and ask a person: no move can make progress",
}

SYSTEM = (
    "You park a car in a yard slot beside the driving lane, one move at a time. Each move drives "
    "about 1 m at walking pace (a nudge 0.3 m); then the car stops, you see where it is, and you "
    "choose again. A safety check stops any move that would bring the car too close to something, "
    "and tells you.\n"
    "How the wheel works: driving forward, wheel right turns the nose right and wheel left turns it "
    "left. Reversing, wheel right swings the nose left (the tail goes right), and wheel left swings "
    "the nose right.\n"
    "How to get into the slot: while the car is more than about 1.5 m to the side of the slot's "
    "centre line, drive forward with the wheel toward the slot so the nose angles in by 20 to 40 "
    "degrees. As it nears the line, turn the wheel the other way so it arrives straight. A car that "
    "is straight and on the line only needs straight moves: forward if it is short of the slot's "
    "centre, back if it is past it, nudges for the last half metre. If there is little free space "
    "in front, back up with the wheel turned to change the angle, then go forward again. Choose "
    "parked only when the whole car is inside the slot's lines and nearly straight. Choose ask a "
    "person only when no move can make progress.\n"
    "Guide lines tell you where each move would end. Think two or three moves ahead: angling in "
    "first makes the heading worse before the car can straighten into the slot, and a car that "
    "reaches the slot's line still angled must straighten by driving forward with the wheel away "
    "from the slot (or back up with the wheel toward it). Do not repeat a pair of moves that undo "
    "each other."
)


# ---------- geometry, in the slot's frame ----------

@dataclass(frozen=True)
class Pose:
    x: float                        # m along the slot; 0 is the slot's centre
    y: float                        # m to the slot's left
    yaw: float                      # rad, counter-clockwise from the slot's direction

    @property
    def yaw_deg(self) -> float:
        return math.degrees(self.yaw)


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    yaw: float
    length: float
    width: float
    label: str = ""

    @cached_property
    def shape(self) -> tuple[list[tuple[float, float]], tuple[float, float, float, float]]:
        """Corners and axis-aligned bounds, computed once: an obstacle is tested many times."""
        c = self.corners()
        xs, ys = [p[0] for p in c], [p[1] for p in c]
        return c, (min(xs), max(xs), min(ys), max(ys))

    def corners(self, margin: float = 0.0) -> list[tuple[float, float]]:
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        hl, hw = self.length / 2 + margin, self.width / 2 + margin
        return [(self.x + c * dx - s * dy, self.y + s * dx + c * dy)
                for dx, dy in ((hl, hw), (hl, -hw), (-hl, -hw), (-hl, hw))]


def _overlap(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    """Separating-axis test for two convex quadrilaterals."""
    for poly in (a, b):
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            nx, ny = y2 - y1, x1 - x2
            pa = [nx * px + ny * py for px, py in a]
            pb = [nx * px + ny * py for px, py in b]
            if max(pa) < min(pb) or max(pb) < min(pa):
                return False
    return True


def car_box(pose: Pose) -> Box:
    return Box(pose.x, pose.y, pose.yaw, CAR_LENGTH_M, CAR_WIDTH_M, "this car")


def touching(pose: Pose, obstacles: list[Box], margin: float = 0.0) -> Box | None:
    """The first obstacle the car at this pose, grown by margin, overlaps."""
    mine = car_box(pose).corners(margin)
    x0, x1 = min(p[0] for p in mine), max(p[0] for p in mine)
    y0, y1 = min(p[1] for p in mine), max(p[1] for p in mine)
    for box in obstacles:
        corners, (bx0, bx1, by0, by1) = box.shape
        if bx0 > x1 or bx1 < x0 or by0 > y1 or by1 < y0:       # bounds apart: cannot touch
            continue
        if _overlap(mine, corners):
            return box
    return None


def advance(pose: Pose, direction: int, wheel: float, distance: float, step: float = 0.05) -> Pose:
    """Bicycle model: drive `distance` m forward (direction 1) or back (-1) with the wheel at
    `wheel` (-1 full right .. 1 full left) times STEER_DEG."""
    curvature = math.tan(math.radians(STEER_DEG * wheel)) / WHEELBASE_M
    x, y, yaw = pose.x, pose.y, pose.yaw
    left = distance
    while left > 1e-9:
        ds = min(step, left) * direction
        x += ds * math.cos(yaw)
        y += ds * math.sin(yaw)
        yaw += ds * curvature
        left -= step
    return Pose(x, y, (yaw + math.pi) % (2 * math.pi) - math.pi)


def free_distance(pose: Pose, direction: int, obstacles: list[Box], limit: float = 8.0,
                  step: float = 0.1) -> float:
    """How far the car can drive straight forward (1) or back (-1) before touching something."""
    travelled = 0.0
    while travelled < limit:
        nxt = advance(pose, direction, 0.0, travelled + step, step=step)
        if touching(nxt, obstacles):
            return round(travelled, 1)
        travelled += step
    return limit


def overhang(pose: Pose) -> dict[str, float]:
    """How far the car sticks out over each of the slot's lines, m (0 when inside it)."""
    xs, ys = zip(*car_box(pose).corners())
    hl, hw = SLOT_LENGTH_M / 2, SLOT_WIDTH_M / 2
    return {"front line": max(0.0, max(xs) - hl), "back line": max(0.0, -hl - min(xs)),
            "lane line": max(0.0, max(ys) - hw), "kerb": max(0.0, -hw - min(ys))}


def is_parked(pose: Pose) -> bool:
    xs, ys = zip(*car_box(pose).corners())
    hl, hw = SLOT_LENGTH_M / 2 - PARKED_INSET_END_M, SLOT_WIDTH_M / 2 - PARKED_INSET_SIDE_M
    return (abs(pose.yaw_deg) <= PARKED_YAW_DEG and max(xs) <= hl and min(xs) >= -hl
            and max(ys) <= hw and min(ys) >= -hw)


def kerb(slot_width: float = SLOT_WIDTH_M) -> Box:
    """The kerb on the far side of the shoulder, as an obstacle."""
    return Box(0.0, -slot_width / 2 - 1.0, 0.0, 80.0, 2.0, "the kerb")


def to_slot_frame(slot_x: float, slot_y: float, slot_yaw_deg: float,
                  x: float, y: float, yaw_deg: float) -> Pose:
    """A CARLA pose (left-handed: y to the right, yaw clockwise from above) in the slot's frame."""
    s = math.radians(slot_yaw_deg)
    dx, dy = x - slot_x, y - slot_y
    along = dx * math.cos(s) + dy * math.sin(s)
    right = -dx * math.sin(s) + dy * math.cos(s)
    yaw = -math.radians(yaw_deg - slot_yaw_deg)
    return Pose(along, -right, (yaw + math.pi) % (2 * math.pi) - math.pi)


# ---------- the paths Gemini chooses between ----------

@dataclass(frozen=True)
class Path:
    name: str                       # "A", "B", "C"
    moves: tuple[str, ...]
    poses: tuple[Pose, ...]         # sampled along it, for drawing and clearance
    metres: float
    changes: int                    # changes between forward and reverse
    closest_m: float                # nearest any point of the car comes to an obstacle, capped
    closest_to: str
    strategy: str = ""

    @property
    def option(self) -> str:
        return f"path_{self.name}"

    def describe(self) -> str:
        legs, last, run = [], None, 0.0
        for m in self.moves:
            direction, _w, d = MOVES[m]
            if last is not None and direction != last:
                legs.append(f"{'forward' if last > 0 else 'reverse'} {run:.1f} m")
                run = 0.0
            last, run = direction, run + d
        legs.append(f"{'forward' if last > 0 else 'reverse'} {run:.1f} m")
        near = (f"closest {self.closest_m:.2f} m to {self.closest_to}" if self.closest_m < 1.0
                else "never within 1 m of anything")
        return (f"{self.strategy + ': ' if self.strategy else ''}{self.metres:.1f} m ({', then '.join(legs)}), "
                f"{self.changes} change{'s' if self.changes != 1 else ''} of direction, {near}")

    def segment(self) -> list[str]:
        """The moves up to the first change of direction, at most three."""
        first = MOVES[self.moves[0]][0]
        out = []
        for m in self.moves:
            if MOVES[m][0] != first or len(out) == 3:
                break
            out.append(m)
        return out


def _clearance(poses, obstacles: list[Box]) -> tuple[float, str]:
    steps = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0)
    best, label = 1.0, ""
    for p in poses:
        for m in steps:
            hit = touching(p, obstacles, m)
            if hit is not None:
                if m - 0.05 < best or not label:
                    best, label = min(best, round(m - 0.05, 2)), hit.label
                break
    return max(best, 0.0), label or "anything"


def _trace(pose: Pose, moves) -> list[Pose]:
    out, q = [pose], pose
    for m in moves:
        direction, wheel, distance = MOVES[m]
        for _ in range(int(round(distance / 0.25))):
            q = advance(q, direction, wheel, 0.25)
            out.append(q)
    return out


# The strategies the planner proposes: (name, the manoeuvres it may use, safety margin it keeps).
STRATEGIES = (
    ("standard", None, None),
    ("keeps 0.5 m from everything", None, 0.5),
    ("forward only", tuple(m for m, (d, _w, _l) in MOVES.items() if d > 0), None),
)
# Weighted A*: follows the estimate harder and finds a way in far sooner (on the CARLA host's CPU
# plain A* took 26 s where the car can wait seconds), for paths a little longer than the shortest.
HEURISTIC_WEIGHT = 2.0
PLAN_BUDGET_S = 10.0                # for all strategies together; the car is stopped meanwhile


def _still_clear(pose: Pose, moves, obstacles: list[Box]) -> bool:
    q = pose
    for m in moves:
        q = _drive_safely(q, m, obstacles, SAFETY_MARGIN_M + 0.05, step=0.2)
        if q is None:
            return False
    return is_parked(q)


def candidate_paths(pose: Pose, obstacles: list[Box], budget_s: float | None = None,
                    current: tuple[str, ...] = ()) -> list[Path]:
    """Distinct ways into the slot from here, one per strategy that finds one, shortest first.

    `current` is the rest of the path chosen last time; if it still gets in safely from here it is
    offered as it is ("the path chosen before"), so the car always has a way to keep going even when
    a fresh search runs out of time.
    """
    import time as _time

    budget = PLAN_BUDGET_S if budget_s is None else budget_s
    deadline = _time.monotonic() + budget
    found, seen = [], set()
    if current and _still_clear(pose, current, obstacles):
        dirs = [MOVES[m][0] for m in current]
        changes = sum(1 for a, b in zip(dirs, dirs[1:]) if a != b)
        metres = sum(MOVES[m][2] for m in current)
        found.append((metres + 1.5 * changes, "the path chosen before", tuple(current), metres, changes))
        seen.add(tuple(current))
    for strategy, allowed, margin in STRATEGIES:
        # each strategy its own share: one with no answer (forward only, with a car behind) would
        # otherwise spend the whole budget and leave nothing for the rest
        share = min(deadline, _time.monotonic() + budget / len(STRATEGIES))
        moves = plan_moves(pose, obstacles, max_nodes=30000, moves=allowed, margin=margin, deadline=share)
        if not moves or tuple(moves) in seen:
            continue
        seen.add(tuple(moves))
        dirs = [MOVES[m][0] for m in moves]
        changes = sum(1 for a, b in zip(dirs, dirs[1:]) if a != b)
        metres = sum(MOVES[m][2] for m in moves)
        found.append((metres + 1.5 * changes, strategy, tuple(moves), metres, changes))
    found.sort()
    paths = []
    for _cost, strategy, moves, metres, changes in found[:3]:     # the three best, lettered A-C
        poses = _trace(pose, moves)
        closest, to = _clearance(poses[1:], obstacles)
        paths.append(Path("ABC"[len(paths)], moves, tuple(poses), metres, changes, closest, to, strategy))
    return paths


# ---------- what Gemini is shown ----------

@dataclass
class ParkView:
    vin: str
    slot: str
    pose: Pose
    obstacles: list[Box]
    ahead_m: float
    behind_m: float
    decisions: int
    last_moves: list[str]
    note: str | None = None
    # move -> where it would end, or None if the safety check would stop it: the guide lines
    outcomes: dict = field(default_factory=dict)
    mode: str = "moves"
    paths: list = field(default_factory=list)

    @property
    def options(self) -> tuple[str, ...]:
        if self.mode == "moves":
            return OPTIONS
        if is_parked(self.pose):
            return ("parked", "ask_a_person")
        return tuple(p.option for p in self.paths) + ("ask_a_person",)

    def truth(self) -> dict:
        """Simulation ground truth for the offline stand-in; never sent to Gemini."""
        return {"pose": [self.pose.x, self.pose.y, self.pose.yaw], "options": list(self.options),
                "obstacles": [[b.x, b.y, b.yaw, b.length, b.width, b.label] for b in self.obstacles]}


def _side(value: float, positive: str, negative: str) -> str:
    return positive if value >= 0 else negative


def _outside(pose: Pose) -> str:
    over = [f"{v:.1f} m over the {k}" for k, v in overhang(pose).items() if v >= 0.05]
    return ", ".join(over) if over else "nothing, the car is inside them"


def _where(p: Pose) -> str:
    if is_parked(p):
        return "PARKED"
    along = f"{abs(p.x):.1f} m {'short' if p.x < 0 else 'past'}"
    side = f"{abs(p.y):.1f} m {'left' if p.y >= 0 else 'right'}"
    nose = f"nose {abs(p.yaw_deg):.0f} deg {'left' if p.yaw >= 0 else 'right'}"
    over = sum(overhang(p).values())
    return f"{along}, {side}, {nose}" + (f", {over:.1f} m outside the lines" if over >= 0.05 else ", inside the lines")


SYSTEM_PATHS = (
    "You decide how a car parks in a yard slot beside the driving lane. A planner proposes paths into "
    "the slot, drawn on the bird's-eye view in colour with their letter, and measured: length, "
    "changes between forward and reverse, and how close the car comes to anything on the way. You "
    "choose one. The car drives the first part of it at walking pace and stops; then you see new "
    "paths from where it is and choose again. A safety check stops the car if anything comes too "
    "close. Prefer the path that is simple and keeps its distance: fewer changes of direction, more "
    "room to other cars and to people. Take a little more length for clearly more room. Choose parked "
    "when the car is inside the slot's lines and nearly straight. Choose ask a person when no path "
    "looks safe, for example a person in the way that the paths do not account for."
)


def prompt(view: ParkView) -> str:
    if view.mode == "paths":
        return _paths_prompt(view)
    p = view.pose
    along = (f"{abs(p.x):.1f} m {_side(-p.x, 'short of', 'past')} the slot's centre"
             if abs(p.x) >= 0.05 else "level with the slot's centre")
    side = (f"{abs(p.y):.1f} m to the {_side(p.y, 'left', 'right')} of the slot's centre line, so the "
            f"slot is to the car's {_side(p.y, 'right', 'left')}" if abs(p.y) >= 0.05
            else "on the slot's centre line")
    heading = (f"the nose points {abs(p.yaw_deg):.0f} degrees to the {_side(p.yaw, 'left', 'right')} of "
               "the slot's direction" if abs(p.yaw_deg) >= 0.5 else "straight along the slot")
    lines = [
        f"Park car {view.vin} in yard slot {view.slot}. The picture is a bird's-eye view: the target "
        "slot is the amber outline, its arrow the way the car must face; the car you are parking is "
        "blue with a white nose; grey boxes are other cars; the light band at the bottom is the kerb.",
        "",
        "Measured now, relative to the slot:",
        f"- along: the car's centre is {along}",
        f"- sideways: {side}",
        f"- heading: {heading}",
        f"- free space: {view.ahead_m:.1f} m in front, {view.behind_m:.1f} m behind",
        f"- outside the slot's lines: {_outside(p)}",
        f"Parked means the whole car inside the slot's lines, at least {PARKED_INSET_SIDE_M} m from each "
        f"side line, and the nose within {PARKED_YAW_DEG:.0f} degrees of the slot's direction.",
        f"Moves so far: {view.decisions}."
        + (f" Last: {', '.join(OPTION_TEXT[m] for m in view.last_moves)}." if view.last_moves else ""),
    ]
    if view.note:
        lines.append(view.note)
    if view.outcomes:
        lines.append("")
        lines.append("Guide lines - where each move would end:")
        for move, end in view.outcomes.items():
            lines.append(f"- {move}: " + (_where(end) if end is not None else "stopped by the safety check"))
    lines.append("")
    lines.append("Options, in words: " + "; ".join(f"{o} = {OPTION_TEXT[o]}" for o in OPTIONS) + ".")
    lines.append("Choose the next move.")
    return "\n".join(lines)


def _paths_prompt(view: ParkView) -> str:
    p = view.pose
    lines = [
        f"Car {view.vin} is parking in yard slot {view.slot}. The picture is a bird's-eye view: the "
        "target slot is the amber outline, its arrow the way the car must face; the car is blue with "
        "a white nose; grey boxes are other cars; the light band at the bottom is the kerb; the "
        "coloured lines are the proposed paths, lettered at their ends.",
        "",
        f"Now: {_where(p)}. Decisions so far: {view.decisions}.",
    ]
    if view.note:
        lines.append(view.note)
    lines.append("")
    if is_parked(p):
        lines.append("The car is parked: the whole car is inside the slot's lines and nearly straight.")
        lines.append("- parked: stop here, parking is done")
    else:
        lines.append("Proposed paths into the slot:")
        for path in view.paths:
            lines.append(f"- {path.option}: {path.describe()}")
    lines.append("- ask_a_person: stop and ask a person")
    lines.append("")
    lines.append("Choose.")
    return "\n".join(lines)


PATH_COLOURS = [(0, 200, 255), (255, 90, 200), (150, 230, 90)]


def render(view: ParkView, scale: float = 26.0, size: tuple[int, int] = (640, 400)) -> bytes:
    """The bird's-eye view as PNG: slot direction to the right, the lane at the top."""
    from PIL import Image, ImageDraw

    w, h = size
    cx, cy = -3.0, 1.75

    def px(x: float, y: float) -> tuple[float, float]:
        return (w / 2 + (x - cx) * scale, h / 2 - (y - cy) * scale)

    img = Image.new("RGB", size, (58, 62, 68))
    d = ImageDraw.Draw(img)
    half = SLOT_WIDTH_M / 2
    d.polygon([px(-40, half), px(40, half), px(40, -half), px(-40, -half)], fill=(74, 78, 86))
    d.polygon([px(-40, -half), px(40, -half), px(40, -20), px(-40, -20)], fill=(170, 168, 158))
    d.line([px(-40, half), px(40, half)], fill=(235, 235, 235), width=2)
    x = -40.0
    while x < 40:                                       # lane centre, dashed
        d.line([px(x, half + 3.5), px(x + 2.0, half + 3.5)], fill=(235, 235, 235), width=2)
        x += 5.0
    for k in range(-4, 5):                              # slot dividers
        sx = SLOT_LENGTH_M / 2 + k * SLOT_LENGTH_M
        d.line([px(sx, half), px(sx, -half)], fill=(205, 205, 205), width=1)
    hl = SLOT_LENGTH_M / 2
    d.polygon([px(-hl, half), px(hl, half), px(hl, -half), px(-hl, -half)], outline=(227, 155, 46))
    d.rectangle([*px(-hl, half), *px(hl, -half)], outline=(227, 155, 46), width=3)
    d.line([px(-1.2, 0), px(1.2, 0)], fill=(227, 155, 46), width=3)
    d.polygon([px(1.2, 0.45), px(1.9, 0), px(1.2, -0.45)], fill=(227, 155, 46))
    d.text(px(-hl + 0.2, half - 0.25), view.slot, fill=(227, 155, 46))
    for box in view.obstacles:
        if box.label == "the kerb":
            continue
        d.polygon([px(*c) for c in box.corners()], fill=(150, 154, 160), outline=(30, 30, 30))
    for path, colour in zip(view.paths, PATH_COLOURS):
        pts = [px(q.x, q.y) for q in path.poses]
        if len(pts) > 1:
            d.line(pts, fill=colour, width=3)
        end = path.poses[-1]
        d.text(px(end.x + 0.3, end.y + 0.9), path.name, fill=colour)
    car = car_box(view.pose)
    d.polygon([px(*c) for c in car.corners()], fill=(47, 95, 196), outline=(255, 255, 255))
    p = view.pose
    nose = (p.x + math.cos(p.yaw) * CAR_LENGTH_M / 2, p.y + math.sin(p.yaw) * CAR_LENGTH_M / 2)
    back = (p.x + math.cos(p.yaw) * (CAR_LENGTH_M / 2 - 1.0), p.y + math.sin(p.yaw) * (CAR_LENGTH_M / 2 - 1.0))
    sx, sy = -math.sin(p.yaw) * 0.6, math.cos(p.yaw) * 0.6
    d.polygon([px(*nose), px(back[0] + sx, back[1] + sy), px(back[0] - sx, back[1] - sy)], fill=(255, 255, 255))
    d.line([px(cx - 11.5, cy + 7.0), px(cx - 10.5, cy + 7.0)], fill=(255, 255, 255), width=3)
    d.text(px(cx - 11.5, cy + 6.85), "1 m", fill=(255, 255, 255))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# ---------- the session ----------

@dataclass(frozen=True)
class Command:
    speed: float                    # m/s target; negative is reverse; 0 with brake to stop
    wheel: float = 0.0              # -1 full right .. 1 full left
    brake: bool = False


STOP = Command(0.0, 0.0, True)


@dataclass
class ParkingSession:
    """One car parking in one slot. Asks for a decision, drives it safely, asks again.

    The caller loops: while `needs_decision`, show Gemini `view()` and pass its Decision to
    `apply()`; otherwise apply `control()` to the car each tick. `outcome` is set when it is over:
    "parked", "gave_up" or "asked".
    """

    vin: str
    slot: str
    mode: str = "paths"             # paths | moves; see the module note
    max_decisions: int = MAX_DECISIONS
    phase: str = "decide"           # decide | move | settle | done
    outcome: str | None = None
    decisions: int = 0
    refused: int = 0
    safety_stops: int = 0
    moves: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    note: str | None = None
    _move: tuple[int, int, float] | None = None
    _queue: list[str] = field(default_factory=list)   # the rest of the chosen path's segment
    _rest: list[str] = field(default_factory=list)    # the chosen path after this segment
    _travelled: float = 0.0
    _elapsed: float = 0.0

    @property
    def done(self) -> bool:
        return self.phase == "done"

    @property
    def needs_decision(self) -> bool:
        if self.phase == "decide" and self.decisions >= self.max_decisions:
            self.finish("gave_up")
        return self.phase == "decide"

    def view(self, pose: Pose, obstacles: list[Box]) -> ParkView:
        ahead, behind = free_distance(pose, 1, obstacles), free_distance(pose, -1, obstacles)
        if self.mode == "paths":
            paths = [] if is_parked(pose) else candidate_paths(pose, obstacles, current=tuple(self._rest))
            if not paths and not is_parked(pose):
                # nothing to choose between: no way into the slot from here, so a person decides
                self.note = "The planner found no path into the slot."
                self.finish("asked")
            return ParkView(self.vin, self.slot, pose, list(obstacles), ahead, behind, self.decisions,
                            self.moves[-3:], self.note, mode="paths", paths=paths)
        return ParkView(self.vin, self.slot, pose, list(obstacles), ahead, behind,
                        self.decisions, self.moves[-3:], self.note,
                        {m: _drive_safely(pose, m, obstacles) for m in MOVES})

    def apply(self, decision, pose: Pose, latency_s: float | None = None, view: ParkView | None = None) -> None:
        choice = decision.choice
        self.decisions += 1
        self.trace.append({"n": self.decisions, "choice": choice, "p": round(decision.confidence, 3),
                           "x": round(pose.x, 2), "y": round(pose.y, 2), "yaw_deg": round(pose.yaw_deg, 1),
                           "latency_s": None if latency_s is None else round(latency_s, 2)})
        self.note = None
        if choice == "parked":
            if is_parked(pose):
                self.finish("parked")
                return
            self.refused += 1
            self.note = (f"You chose parked, but the car is not parked yet ({_outside(pose)}; nose "
                         f"{abs(pose.yaw_deg):.0f} degrees off). Keep going.")
            if self.refused >= MAX_REFUSED:
                self.finish("gave_up")
            return
        if choice == "ask_a_person":
            self.finish("asked")
            return
        if choice.startswith("path_"):
            path = next((p for p in (view.paths if view else []) if p.option == choice), None)
            if path is None:
                self.note = f"{choice} was not one of the paths offered. Choose again."
                return
            segment = path.segment()
            self._rest = list(path.moves[len(segment):])
            self.trace[-1]["segment"] = segment
            self.trace[-1]["path"] = list(path.moves)
            self.trace[-1]["strategy"] = path.strategy
            choice, self._queue = segment[0], segment[1:]
        self._start(choice)

    def _start(self, choice: str) -> None:
        self.moves.append(choice)
        self._move = MOVES[choice]
        self._travelled = self._elapsed = 0.0
        self.phase = "move"

    def control(self, pose: Pose, obstacles: list[Box], speed: float, dt: float) -> Command:
        """This tick's command. `speed` is the car's signed speed along its heading, m/s."""
        if self.phase == "move":
            direction, wheel, distance = self._move
            if self._travelled >= distance:
                if self._queue:                              # the next move of the chosen segment
                    self._start(self._queue.pop(0))
                    return Command(0.0, float(MOVES[self.moves[-1]][1]))
                self.phase = "settle"
                return STOP
            ahead = advance(pose, direction, wheel, SAFETY_LOOKAHEAD_M)
            hit = self._unsafe(pose, ahead, obstacles)
            if hit is not None:
                self.safety_stops += 1
                self._queue, self._rest = [], []
                self.note = (f"The safety check stopped your last move after {self._travelled:.1f} m: "
                             f"going on would have brought the car within {SAFETY_MARGIN_M} m of "
                             f"{hit.label or 'an obstacle'}. Choose a different move.")
                self.phase = "settle"
                return STOP
            self._travelled += abs(speed) * dt
            self._elapsed += dt
            if self._elapsed > MAX_MOVE_S:
                self.phase = "settle"
                return STOP
            target = FORWARD_MPS if direction > 0 else -REVERSE_MPS
            return Command(target, float(wheel))
        if self.phase == "settle" and abs(speed) < 0.05:
            self.phase = "done" if self.outcome else "decide"
        return STOP

    @staticmethod
    def _unsafe(now: Pose, ahead: Pose, obstacles: list[Box], margin: float | None = None) -> Box | None:
        """An obstacle the next bit of this move would come too close to.

        A car already inside the margin of something (tight between two cars) may still move,
        as long as it does not get closer: only touching at all, or newly entering the margin, stops it.
        """
        margin = SAFETY_MARGIN_M if margin is None else margin
        for box in obstacles:
            if touching(ahead, [box], 0.05):
                return box
            if touching(ahead, [box], margin) and not touching(now, [box], margin):
                return box
        return None

    def finish(self, outcome: str) -> None:
        self.outcome = outcome
        self.phase = "done"


# ---------- who decides ----------

class GeminiParker:
    """Asks the LLM for the next path or manoeuvre as a decision ("drive.park")."""

    task = "drive.park"

    def __init__(self, llm, mode: str = "paths"):
        self.llm = llm
        self.mode = mode

    def decide(self, view: ParkView, image: bytes | None = None):
        from marshal_ai.llm import Media, decision_schema

        media = [Media("image/png", data=image or render(view), label="bird's-eye view", truth=view.truth())]
        return self.llm.generate(self.task, prompt(view), decision_schema(*view.options), media=media,
                                 system=SYSTEM_PATHS if view.mode == "paths" else SYSTEM)


@lru_cache(maxsize=None)
def _primitive(move: str, step: float):
    """A move from the origin: (pose, lookahead pose) every `step` m along it, and where it ends.
    Driving a move from any pose is then a rigid transform of these, not a fresh integration."""
    direction, wheel, distance = MOVES[move]
    origin, samples, travelled = Pose(0.0, 0.0, 0.0), [], 0.0
    while distance - travelled > 1e-9:
        here = advance(origin, direction, wheel, travelled) if travelled else origin
        samples.append((here, advance(here, direction, wheel, SAFETY_LOOKAHEAD_M)))
        travelled += step
    return tuple(samples), advance(origin, direction, wheel, distance)


def _place(p: Pose, rel: Pose) -> Pose:
    c, s = math.cos(p.yaw), math.sin(p.yaw)
    return Pose(p.x + c * rel.x - s * rel.y, p.y + s * rel.x + c * rel.y,
                (p.yaw + rel.yaw + math.pi) % (2 * math.pi) - math.pi)


def _drive_safely(p: Pose, move: str, obstacles: list[Box], margin: float | None = None,
                  step: float = 0.1) -> Pose | None:
    """Where a move ends, or None if the safety check (at this margin) would stop it on the way."""
    samples, end = _primitive(move, step)
    for here, ahead in samples:
        q, a = _place(p, here), _place(p, ahead)
        # the live check too: a car already inside a wider planning margin is let through by it,
        # and the live check, at its own margin, would then stop the move (it did, six times running)
        if (ParkingSession._unsafe(q, a, obstacles, margin) is not None
                or ParkingSession._unsafe(q, a, obstacles) is not None):
            return None
    return _place(p, end)


def _drive_safely_slow(p: Pose, move: str, obstacles: list[Box], margin: float | None = None,
                       step: float = 0.1) -> Pose | None:
    """The same, integrated step by step: the reference _drive_safely is checked against."""
    direction, wheel, distance = MOVES[move]
    q, left = p, distance
    while left > 1e-9:
        ahead = advance(q, direction, wheel, SAFETY_LOOKAHEAD_M)
        # the live check too: a car already inside a wider planning margin is let through by it,
        # and the live check, at its own margin, would then stop the move (it did, six times running)
        if (ParkingSession._unsafe(q, ahead, obstacles, margin) is not None
                or ParkingSession._unsafe(q, ahead, obstacles) is not None):
            return None
        q = advance(q, direction, wheel, min(step, left))
        left -= step
    return q


def plan_moves(pose: Pose, obstacles: list[Box], max_nodes: int = 12000, moves=None,
               margin: float | None = None, deadline: float | None = None) -> list[str] | None:
    """The shortest sequence of manoeuvres that parks the car, by A* search; None if none found.

    The offline stand-in for Gemini and the reference the eval compares it to. Cost is metres
    driven plus a metre for each change between forward and reverse; states are binned at 0.2 m
    and 4 degrees so the search ends.
    """
    import heapq

    def key(p: Pose) -> tuple:
        return (round(p.x / 0.2), round(p.y / 0.2), round(math.degrees(p.yaw) / 4.0))

    def h(p: Pose) -> float:
        return abs(p.x) * 0.5 + abs(p.y) + abs(p.yaw) * 2.0

    if is_parked(pose):
        return []
    start = (h(pose), 0.0, 0, pose, [], 0)
    frontier, seen, count = [start], {key(pose)}, 0
    import time as _time

    allowed = list(moves or MOVES)
    # The plan is checked every 0.2 m, the live safety check every tick: plan with a little more
    # room, or a planned move can be stopped part way (it was, five times on one eval case).
    margin = (SAFETY_MARGIN_M if margin is None else margin) + 0.05
    while frontier and count < max_nodes:
        if deadline is not None and _time.monotonic() > deadline:
            return None
        _f, g, _n, p, path, last_dir = heapq.heappop(frontier)
        for move in allowed:
            q = _drive_safely(p, move, obstacles, margin, step=0.2)
            if q is None:
                continue
            if is_parked(q):
                return path + [move]
            k = key(q)
            if k in seen:
                continue
            seen.add(k)
            direction, _wheel, distance = MOVES[move]
            g2 = g + distance + 0.3 + (1.0 if last_dir and direction != last_dir else 0.0)
            count += 1
            heapq.heappush(frontier, (g2 + HEURISTIC_WEIGHT * h(q), g2, count, q, path + [move], direction))
    return None


def planned_move(pose: Pose, obstacles: list[Box]) -> str:
    """The next manoeuvre of the planned sequence; "ask_a_person" if no plan parks the car."""
    if is_parked(pose):
        return "parked"
    plan = plan_moves(pose, obstacles)
    if not plan:
        return "ask_a_person"
    return plan[0]
