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


class YardTraffic:
    def __init__(self, app: "MarshalApp"):
        self.app = app
        self.rt = app.rt
        self.release_interval_s: float | None = None
        self.handled: set[str] = set()
        self.decisions: list[dict] = []           # every exception decision, with its reason
        self.slot_missions: dict[str, str] = {}   # mission id -> slot

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
        car = self.rt.store.get(Car, vin)
        report = self.rt.store.get(CarReport, vin)
        if not car.do_not_ship and (report is None or report.verdict != Verdict.PASS):
            raise ValueError(f"{vin} has no PASS report; it cannot go to a shipping slot")
        slot = self.pick_slot(car)
        if slot is None:
            await self.handle_exception("yard_full", {"vin": vin})
            return None
        mission = MissionGo(mission_id=self.rt.next_id("M", 4), vin=vin, waypoints=[slot],
                            max_speed_kmh=YARD_SPEED_KMH)
        await self.rt.missions.send(mission)
        self.slot_missions[mission.mission_id] = slot
        update_car(self.rt, vin, yard_slot=slot, mission_id=mission.mission_id)
        return slot

    async def on_mission_done(self, event: MissionDone) -> None:
        car = self.rt.store.get(Car, event.vin)
        if car is None:
            return
        if event.location.startswith("yard.Y"):
            update_car(self.rt, event.vin, state=CarState.YARD, location=event.location,
                       mission_id=None, yard_at=car.yard_at or self.rt.clock())
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
        if len(waiting) >= LINE_BACKLOG_LIMIT or \
                self.rt.plant.line_rate_per_h > 1.1 * self.rt.plant.yard_rate_per_h:
            found.append(("line_outpacing_yard", "line_outpacing_yard",
                          {"cars_waiting": len(waiting), "line_rate_per_h": self.rt.plant.line_rate_per_h,
                           "yard_rate_per_h": self.rt.plant.yard_rate_per_h}))
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

    async def execute(self, plan: YardPlan) -> list[str]:
        done = []
        if plan.release_interval_s:
            self.release_interval_s = plan.release_interval_s
        for a in plan.actions:
            try:
                if a.action == "go":
                    mission = MissionGo(mission_id=self.rt.next_id("M", 4), vin=a.vin,
                                        waypoints=a.waypoints, max_speed_kmh=YARD_SPEED_KMH)
                    await self.rt.missions.send(mission)
                    changes = {"mission_id": mission.mission_id}
                    slot = next((w for w in reversed(a.waypoints) if w.startswith("yard.Y")), None)
                    if slot:
                        changes["yard_slot"] = slot
                    update_car(self.rt, a.vin, **changes)
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

    async def tick(self) -> None:
        for key, kind, details in self.detect_exceptions():
            await self.handle_exception(kind, details, key)
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
        }
