"""Flow 1: Yard traffic controller.

Rules handle routine release and routing every tick; Gemini is called only when a
trade-off or exception needs judgement. A plan that touches several cars or a zone
waits for a supervisor.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from marshal_ai.driving import CommandRejected, MissionDone, MissionGo, MissionHold, MissionResume
from marshal_ai.flows import route
from marshal_ai.flows.base import ask, cars_in, log, update_car
from marshal_ai.llm import to_prompt_json
from marshal_ai.records import Car, CarReport, CarState, Verdict

if TYPE_CHECKING:
    from marshal_ai.app import MarshalApp

GATE_QUEUE_LIMIT = 2
MIN_SPACING_M = 25.0
GATE_SPEED_KMH = 5.0
YARD_SPEED_KMH = 10.0
TRUCK_DELAY_THRESHOLD_MIN = 15.0
LINE_BACKLOG_LIMIT = 6
SLOT_BACKLOG_LIMIT = 3          # passed cars waiting for a yard slot: the yard is not keeping up
ZONE_LEVEL = {"track_closed", "line_outpacing_yard"}


class YardAction(BaseModel):
    vin: str
    action: Literal["go", "hold", "resume"]
    waypoints: list[str] = Field(default_factory=list, description="named map points, for go")
    duration_s: float | None = Field(default=None, description="for hold; empty means until resumed")
    do_not_ship: bool = False
    flag: str | None = Field(default=None, description="e.g. untested")
    reason: str


class YardPlan(BaseModel):
    summary: str
    reasoning: str = Field(description="the trade-off in one or two sentences")
    actions: list[YardAction]
    release_interval_s: float | None = Field(
        default=None, description="minimum seconds between line releases, when slowing the line")


SYSTEM = """You are the Yard Traffic Controller in a car plant. An existing driving system does all
driving; you only plan missions (go, hold, resume) for it. Use only the named map points given.
Cars for the next truck belong in slots nearest the loading bay. A do_not_ship car never goes to the
loading bay. Explain the trade-off briefly. Plan the fewest moves that solve the exception."""


def target_slot(a: YardAction) -> str | None:
    """The yard slot a go action ends in, if it ends in one."""
    return next((w for w in reversed(a.waypoints) if w.startswith("yard.Y")), None)


class YardTraffic:
    def __init__(self, app: "MarshalApp"):
        self.app = app
        self.rt = app.rt
        self.release_interval_s: float | None = None
        self.handled: set[str] = set()
        self.decisions: list[dict] = []           # every exception decision, with its reason
        self.slot_missions: dict[str, str] = {}   # mission id -> slot
        # moves waiting for a slot's current car to drive off it: occupant vin -> actions
        self.deferred: dict[str, list[YardAction]] = {}
        # vin -> map points of the way it is driving now, so the next car's route choice sees it
        self.routes_in_use: dict[str, list[str]] = {}
        self.route_log: list[dict] = []           # every route choice, Gemini's or the rules' (flows/route.py)
        # cars being sent to a slot right now: the route choice awaits Gemini for about a second
        # before the mission is recorded, and the tick, seeing a passed car with no mission, sent it
        # again (twice for VIN-P3 on CARLA, 2026-10-15)
        self.sending: set[str] = set()

    # ---------- routine rules (no model call) ----------

    def gate_queue(self) -> list[Car]:
        return cars_in(self.rt, CarState.INSPECTION_GATE)

    def line_end_xy(self) -> tuple[float, float]:
        """The line end in the same frame as Status.x/y.

        The plant map holds abstract coordinates (line_end is 0,0) while a real backend reports
        world coordinates (line_end is 207.27,-13.93 on Town05). Comparing the two made the
        spacing rule compute 207.7 m for a car standing still at the line end, so it always read
        "clear" and cars were released 16 m apart on CARLA. The backend owns its frame.
        """
        reference = getattr(self.rt.driving, "reference_xy", None)
        if reference is not None:
            return reference("line_end")
        point = self.rt.map.points["line_end"]
        return float(point.x), float(point.y)

    def first_segment_clear(self) -> tuple[bool, str]:
        start_x, start_y = self.line_end_xy()
        now = self.rt.clock()
        live = getattr(self.rt.driving, "position_xy", None)
        for car in self.gate_queue():
            if car.location == "inspection_gate":
                continue                      # it has left the first segment
            # Ask the driving system where the car is. Relying on the status stream meant that
            # before the first Status arrived the rule fell back to wall-clock time, which has
            # no fixed relationship to simulated time: measured at 10.3x real time on this
            # host, so an 18 second wall-clock wait is about 180 simulated seconds. Wall clock
            # cannot stand in for distance travelled in either direction.
            here = live(car.vin) if live is not None else None
            if here is None:
                status = self.rt.missions.last_status.get(car.vin)
                here = (status.x, status.y) if status is not None else None
            if here is not None:
                if math.hypot(here[0] - start_x, here[1] - start_y) < MIN_SPACING_M:
                    return False, f"{car.vin} is within {MIN_SPACING_M:.0f} m of line end"
            elif car.released_at is not None:
                min_gap_s = MIN_SPACING_M / (GATE_SPEED_KMH / 3.6)
                if (now - car.released_at).total_seconds() < min_gap_s:
                    return False, f"{car.vin} released less than {min_gap_s:.0f} s ago"
        return True, ""

    def can_release(self) -> tuple[bool, str]:
        if "test_track" in self.rt.plant.closed_zones:
            return False, "test track closed"
        if "inspection_gate" in self.rt.plant.closed_zones:
            return False, "inspection gate closed"
        if len(self.gate_queue()) >= GATE_QUEUE_LIMIT:
            return False, f"gate queue has {GATE_QUEUE_LIMIT} cars"
        clear, why = self.first_segment_clear()
        if not clear:
            return False, why
        if self.release_interval_s:
            last = max((c.released_at for c in self.rt.store.list(Car) if c.released_at), default=None)
            if last and (self.rt.clock() - last).total_seconds() < self.release_interval_s:
                return False, "slowed release interval"
        return True, ""

    def waiting_at_line_end(self) -> list[Car]:
        cars = [c for c in cars_in(self.rt, CarState.LINE_END)
                if c.mission_id is None and "untested" not in c.flags]
        return sorted(cars, key=lambda c: c.updated_at)

    def slots_by_distance(self) -> list[str]:
        slots = [p for p in self.rt.map.points_in_zone("yard") if p.startswith("yard.Y")]
        return sorted(slots, key=lambda p: self.rt.map.distance(p, "loading_bay"))

    def free_slots(self, exclude_vin: str | None = None) -> list[str]:
        taken = {c.yard_slot for c in self.rt.store.list(Car)
                 if c.yard_slot and c.vin != exclude_vin and c.state != CarState.SHIPPED}
        return [s for s in self.slots_by_distance() if s not in taken]

    def pick_slot(self, car: Car) -> str | None:
        """Cars for the next truck get the free slots nearest the loading bay; others the farthest."""
        free = self.free_slots(car.vin)
        if not free:
            return None
        nxt = self.rt.plant.next_truck()
        is_next = nxt is not None and car.build_sheet.truck_id == nxt.truck_id and not car.do_not_ship
        return free[0] if is_next else free[-1]

    async def release_next(self) -> str | None:
        waiting = self.waiting_at_line_end()
        if not waiting:
            return None
        ok, why = self.can_release()
        if not ok:
            return None
        car = waiting[0]
        mission = MissionGo(mission_id=self.rt.next_id("M", 4), vin=car.vin,
                            waypoints=["inspection_gate"], max_speed_kmh=GATE_SPEED_KMH)
        await self.rt.missions.send(mission)
        update_car(self.rt, car.vin, state=CarState.INSPECTION_GATE, mission_id=mission.mission_id,
                   released_at=self.rt.clock())
        log.info("yard: released %s to the inspection gate", car.vin)
        return car.vin

    async def send_to_slot(self, vin: str) -> str | None:
        if vin in self.sending:
            return None
        self.sending.add(vin)
        try:
            return await self._send_to_slot(vin)
        finally:
            self.sending.discard(vin)

    async def _send_to_slot(self, vin: str) -> str | None:
        car = self.rt.store.get(Car, vin)
        report = self.rt.store.get(CarReport, vin)
        if not car.do_not_ship and (report is None or report.verdict != Verdict.PASS):
            raise ValueError(f"{vin} has no PASS report; it cannot go to a shipping slot")
        slot = self.pick_slot(car)
        if slot is None:
            # A full yard is not a judgement: the car waits where it is and the tick sends it on
            # when a slot frees (waiting_for_slot). This used to ask Gemini for a "yard_full" plan
            # every time, about 22 calls in a 40-car shift (python -m marshal_ai.shift), none of
            # which changed anything. A backlog of them is the design's "line running faster than
            # the yard", raised once by detect_exceptions.
            log.info("yard: no free slot for %s; it waits for one", vin)
            return None
        # Which way it drives there: the only way, the shortest when nothing is going on, or
        # Gemini's choice when something is (flows/route.py).
        waypoints, record = await route.choose(
            self.rt, vin, car.location, slot, self.routes_in_use, YARD_SPEED_KMH,
            getattr(self.rt.settings, "route", "reason"))
        mission = MissionGo(mission_id=self.rt.next_id("M", 4), vin=vin, waypoints=waypoints,
                            max_speed_kmh=YARD_SPEED_KMH)
        await self.rt.missions.send(mission)
        self.slot_missions[mission.mission_id] = slot
        self.routes_in_use[vin] = [car.location, *waypoints]
        self.route_log.append(record)
        update_car(self.rt, vin, yard_slot=slot, mission_id=mission.mission_id,
                   route=[car.location, *waypoints], route_by=record["by"])
        return slot

    async def on_mission_done(self, event: MissionDone) -> None:
        car = self.rt.store.get(Car, event.vin)
        if car is None:
            return
        self.routes_in_use.pop(event.vin, None)
        if event.location.startswith("yard.Y"):
            update_car(self.rt, event.vin, state=CarState.YARD, location=event.location,
                       mission_id=None, yard_at=car.yard_at or self.rt.clock())
            for action in self.deferred.pop(event.vin, []):
                await self.go(action)
        elif event.location == "buffer_zone_b":
            update_car(self.rt, event.vin, location=event.location, mission_id=None)
        elif event.location == "inspection_gate":
            # Arrival at the gate was previously recorded by the visual inspector, when the
            # inspection finished. Until then a car standing at the gate still reported
            # location "line_end", which is simply not where it is - and anything waiting for
            # the arrival rather than for the verdict waited forever. Where the car is and what
            # has been decided about it are different facts, so the arrival is recorded here.
            update_car(self.rt, event.vin, location=event.location, mission_id=None)

    def ship(self, truck_id: str) -> list[str]:
        """Load a truck. Only cars in the yard with a PASS report and no hold are loaded."""
        loaded = []
        for car in cars_in(self.rt, CarState.YARD):
            if car.build_sheet.truck_id != truck_id or car.do_not_ship:
                continue
            report = self.rt.store.get(CarReport, car.vin)
            if report is None or report.verdict != Verdict.PASS:
                continue
            update_car(self.rt, car.vin, state=CarState.SHIPPED, yard_slot=None)
            loaded.append(car.vin)
        self.rt.plant.trucks.pop(truck_id, None)
        return loaded

    # ---------- exceptions (Gemini) ----------

    def detect_exceptions(self) -> list[tuple[str, str, dict]]:
        found = []
        for truck in self.rt.plant.trucks.values():
            if truck.delay_min >= TRUCK_DELAY_THRESHOLD_MIN:
                found.append((f"truck_delayed:{truck.truck_id}:{truck.eta.isoformat()}",
                              "truck_delayed",
                              {"truck_id": truck.truck_id, "delay_min": round(truck.delay_min)}))
        waiting = self.waiting_at_line_end()
        if "test_track" in self.rt.plant.closed_zones and waiting:
            found.append(("track_closed", "track_closed", {"cars_waiting": [c.vin for c in waiting]}))
        elif "test_track" not in self.rt.plant.closed_zones:
            self.handled.discard("track_closed")
        for_slot = self.waiting_for_slot()
        outpacing = len(waiting) >= LINE_BACKLOG_LIMIT or len(for_slot) >= SLOT_BACKLOG_LIMIT \
            or self.rt.plant.line_rate_per_h > 1.1 * self.rt.plant.yard_rate_per_h
        if outpacing:
            found.append(("line_outpacing_yard", "line_outpacing_yard",
                          {"cars_waiting": len(waiting), "cars_waiting_for_a_yard_slot": len(for_slot),
                           "line_rate_per_h": self.rt.plant.line_rate_per_h,
                           "yard_rate_per_h": self.rt.plant.yard_rate_per_h}))
        elif not for_slot and len(waiting) < LINE_BACKLOG_LIMIT // 2:
            # Over only once the yard has caught up, not the moment the count dips under the bar:
            # a backlog hovering at it would otherwise be asked about again and again.
            self.handled.discard("line_outpacing_yard")
        return [f for f in found if f[0] not in self.handled]

    def state_for_prompt(self) -> dict:
        cars = self.rt.store.list(Car)
        return {
            "cars": [{"vin": c.vin, "state": c.state.value, "location": c.location,
                      "yard_slot": c.yard_slot, "truck_id": c.build_sheet.truck_id,
                      "do_not_ship": c.do_not_ship, "flags": c.flags}
                     for c in cars if c.state != CarState.SHIPPED],
            "trucks_by_eta": [{"truck_id": t.truck_id, "eta": t.eta.isoformat(),
                               "delay_min": round(t.delay_min)} for t in self.rt.plant.trucks_by_eta()],
            "yard_slots_nearest_loading_bay_first": self.slots_by_distance(),
            "free_slots": self.free_slots(),
            "closed_zones": sorted(self.rt.plant.closed_zones),
            "rework_bays": {b: {"occupants": o, "capacity": self.rt.plant.bay_capacity[b]}
                            for b, o in self.rt.plant.bay_occupants.items()},
            "places": {"line_end": "line_end", "buffer": "buffer_zone_b",
                       "gate": "inspection_gate", "loading": "loading_bay"},
        }

    def check_plan(self, plan: YardPlan) -> list[str]:
        errors = []
        for a in plan.actions:
            car = self.rt.store.get(Car, a.vin)
            if car is None:
                errors.append(f"unknown car {a.vin}")
                continue
            if a.action == "go":
                if not a.waypoints:
                    errors.append(f"{a.vin}: go needs waypoints")
                for wp in a.waypoints:
                    if not self.rt.map.has_point(wp):
                        errors.append(f"{a.vin}: unknown map point {wp}")
                if (car.do_not_ship or a.do_not_ship) and "loading_bay" in a.waypoints:
                    errors.append(f"{a.vin}: do-not-ship car cannot go to loading_bay")
                # The rule send_to_slot keeps, kept for plans too: a shipping slot needs a PASS report.
                # Asked about a backlog at the line end, Gemini planned "move all 6 cars from the line
                # end to available yard slots" - past the gate and the track - and a supervisor
                # approving in a hurry would have shipped six uninspected cars.
                report = self.rt.store.get(CarReport, a.vin)
                passed = report is not None and report.verdict == Verdict.PASS
                if target_slot(a) and not passed and not (car.do_not_ship or a.do_not_ship):
                    errors.append(f"{a.vin}: has no PASS report, so it cannot go to a shipping slot "
                                  f"({target_slot(a)}); hold it, or flag it do_not_ship")
        return errors

    async def handle_exception(self, kind: str, details: dict, key: str | None = None) -> dict:
        self.handled.add(key or kind)
        prompt = (f"Exception: {kind}\nDetails: {to_prompt_json(details)}\n"
                  f"Plant state: {to_prompt_json(self.state_for_prompt())}\n"
                  "Return a plan.")
        try:
            plan = await ask(self.rt, "yard.exception", prompt, YardPlan, system=SYSTEM)
        except Exception as e:          # no plan: nothing moves, the supervisor handles it
            decision = {"kind": kind, "status": "needs_supervisor",
                        "errors": [f"model unavailable: {type(e).__name__}"]}
            self.decisions.append(decision)
            log.warning("yard: no plan for %s, model unavailable (%s)", kind, e)
            return decision
        errors = self.check_plan(plan)
        if errors:
            decision = {"kind": kind, "status": "rejected_plan", "errors": errors, "plan": plan.model_dump()}
            self.decisions.append(decision)
            log.warning("yard: plan for %s rejected: %s", kind, errors)
            return decision
        several = len({a.vin for a in plan.actions}) > 1 or kind in ZONE_LEVEL
        if several:
            proposal = self.rt.propose("replan", plan.summary, {"kind": kind, "plan": plan.model_dump()})
            decision = {"kind": kind, "status": "proposed", "proposal_id": proposal.proposal_id,
                        "summary": plan.summary, "reasoning": plan.reasoning}
        else:
            await self.execute(plan)
            decision = {"kind": kind, "status": "executed", "summary": plan.summary,
                        "reasoning": plan.reasoning}
        self.decisions.append(decision)
        return decision

    async def go(self, a: YardAction) -> None:
        mission = MissionGo(mission_id=self.rt.next_id("M", 4), vin=a.vin,
                            waypoints=a.waypoints, max_speed_kmh=YARD_SPEED_KMH)
        await self.rt.missions.send(mission)
        changes = {"mission_id": mission.mission_id}
        slot = target_slot(a)
        if slot:
            changes["yard_slot"] = slot
        update_car(self.rt, a.vin, **changes)

    def blocked_by(self, plan: YardPlan) -> dict[str, str]:
        """Moves into a slot whose car this same plan moves out: mover vin -> that car's vin.

        A re-slot is often a swap - the next truck's cars into the slots nearest the bay, the late
        truck's cars out of them. Sent at once, a car drives to a slot that is still taken and stops
        short of it, in the yard lane, while the car it is waiting for has to get past. So it waits
        for that car to arrive at its own new slot first. Every move in a cycle (A to B's slot, B to
        A's) is blocked; the first one goes anyway, to break it.
        """
        moving = {a.vin for a in plan.actions if a.action == "go"}
        held_by = {c.yard_slot: c.vin for c in self.rt.store.list(Car)
                   if c.yard_slot and c.state == CarState.YARD}
        blocked = {}
        for a in plan.actions:
            slot = target_slot(a) if a.action == "go" else None
            occupant = held_by.get(slot)
            if occupant and occupant != a.vin and occupant in moving:
                blocked[a.vin] = occupant
        self._cycle_breakers = {}
        for a in plan.actions:                    # in plan order, so the first of a cycle breaks it
            seen, vin = [], a.vin
            while vin in blocked and vin not in seen:
                seen.append(vin)
                vin = blocked[vin]
            if vin in seen:                       # a cycle: A to B's slot, B to A's, ...
                first = next(x.vin for x in plan.actions if x.vin in seen[seen.index(vin):])
                self._cycle_breakers[first] = blocked.pop(first)
        return blocked

    def free_slot(self, plan: YardPlan) -> str | None:
        """A yard slot no car holds and this plan sends no car to: a cycle's car waits there."""
        held = {c.yard_slot for c in self.rt.store.list(Car) if c.yard_slot}
        wanted = {target_slot(a) for a in plan.actions if a.action == "go"}
        free = [p for p in self.rt.map.points if p.startswith("yard.Y") and p not in held | wanted]
        return free[0] if free else None

    async def execute(self, plan: YardPlan) -> list[str]:
        done = []
        if plan.release_interval_s:
            self.release_interval_s = plan.release_interval_s
        blocked = self.blocked_by(plan)
        for a in plan.actions:
            try:
                if a.action == "go" and a.vin in getattr(self, "_cycle_breakers", {}):
                    # Every car of a cycle waits for another, so one has to go first, and its slot
                    # is still taken. Sent straight there it stopped behind that car, and both
                    # waited until the run timed out. It parks in a free slot; once the car in its
                    # slot has moved on, it goes there.
                    occupant, free = self._cycle_breakers[a.vin], self.free_slot(plan)
                    if free is None:
                        await self.go(a)
                    else:
                        await self.go(a.model_copy(update={"waypoints": [free]}))
                        self.deferred.setdefault(occupant, []).append(a)
                        log.info("yard: %s parks in %s until %s leaves %s", a.vin, free, occupant,
                                 target_slot(a))
                elif a.action == "go" and a.vin in blocked:
                    self.deferred.setdefault(blocked[a.vin], []).append(a)
                    log.info("yard: %s waits for %s to leave %s", a.vin, blocked[a.vin], target_slot(a))
                elif a.action == "go":
                    await self.go(a)
                elif a.action == "hold":
                    await self.rt.missions.send(MissionHold(vin=a.vin, duration_s=a.duration_s,
                                                            reason=a.reason))
                else:
                    await self.rt.missions.send(MissionResume(vin=a.vin))
            except (CommandRejected, KeyError) as e:
                log.warning("yard: action for %s failed: %s", a.vin, e)
                continue
            car = self.rt.store.get(Car, a.vin)
            flags = car.flags + ([a.flag] if a.flag and a.flag not in car.flags else [])
            update_car(self.rt, a.vin, do_not_ship=car.do_not_ship or a.do_not_ship, flags=flags)
            done.append(a.vin)
        return done

    async def decide(self, proposal_id: str, approve: bool, by: str) -> dict:
        p = self.rt.decide(proposal_id, "replan", approve, by, what="re-plan")
        moved = await self.execute(YardPlan.model_validate(p.payload["plan"])) if approve else []
        return {"proposal_id": proposal_id, "status": p.status.value, "moved": moved}

    def waiting_for_slot(self) -> list[Car]:
        """Cars with a PASS report that found the yard full, still where their report finished."""
        waiting = []
        for car in self.rt.store.list(Car):
            if car.state in (CarState.YARD, CarState.SHIPPED) or car.mission_id or car.yard_slot \
                    or car.do_not_ship or car.vin in self.sending:
                continue
            report = self.rt.store.get(CarReport, car.vin)
            if report is not None and report.verdict == Verdict.PASS:
                waiting.append(car)
        return sorted(waiting, key=lambda c: c.updated_at)

    async def tick(self) -> None:
        for key, kind, details in self.detect_exceptions():
            await self.handle_exception(kind, details, key)
        # A car that found the yard full used to wait for good: nothing tried again when a truck
        # freed slots, so it was never shipped (found by a 40-car shift, python -m marshal_ai.shift).
        for car in self.waiting_for_slot():
            if not self.free_slots(car.vin):
                break
            await self.send_to_slot(car.vin)
        await self.release_next()

    def kpis(self) -> dict:
        cars = self.rt.store.list(Car)
        line_to_yard = [(c.yard_at - c.released_at).total_seconds()
                        for c in cars if c.yard_at and c.released_at]
        return {
            "cars_waiting_at_line_end": len(self.waiting_at_line_end()),
            "cars_in_yard": sum(c.state == CarState.YARD for c in cars),
            "cars_shipped": sum(c.state == CarState.SHIPPED for c in cars),
            "avg_line_to_yard_s": round(sum(line_to_yard) / len(line_to_yard)) if line_to_yard else None,
            "routes_chosen_by_gemini": sum(r["by"] == "gemini" for r in self.route_log),
            "routes_by_rules": sum(r["by"] == "rules" for r in self.route_log),
        }
