"""A shift on the simulated plant, to measure the design's operational KPIs (flows 1, 2, 3, 4, 5).

    python -m marshal_ai.shift --cars 40 --seed 7
    python -m marshal_ai.shift --cars 40 --miss-rate 0.03     # the gate misses 3% of defects

Cars leave the line at the line rate, each with a fault drawn from a mix (or none): a dead lamp,
the wrong colour, a missing badge, a scratch, a dent, a panel gap. Some get stuck on the way to the
yard, at a box with or without a worker. One truck runs late. A simulated supervisor makes every
human decision after a delay: confirms rework, judges review cards by looking at the car (the
ground truth), approves re-plans, picks the recommended assistance option. Bays finish a repair in
the design's minutes. Trucks load at their ETA and wait up to 30 minutes for their cars.

What this measures is the operation: the rules, hand-offs and timings around the judgements. The
judgements come from the offline stand-ins, which read the ground truth. How well Gemini makes
them is measured on real images by `python -m marshal_ai.eval`. `--miss-rate` connects the two: it
drops that share of the gate's defect findings, as a less than perfect model would, and the shift
shows where the misses end up.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import random
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from marshal_ai.app import MarshalApp
from marshal_ai.config import Settings
from marshal_ai.driving.routines import R12
from marshal_ai.fakeworld import CarTruth, Defect
from marshal_ai.flows.report import BAYS
from marshal_ai.history import WithHistory
from marshal_ai.llm import LLMCall
from marshal_ai.offline import offline_llm
from marshal_ai.plant.state import Truck
from marshal_ai.records import (AssistanceCase, BuildSheet, Car, CarReport, CarState, InspectionResult,
                                Verdict)
from marshal_ai.runtime import SimClock, build_runtime

SUPERVISOR = "supervisor-sim"
OPTIONS = ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"]
CHECKED_LAMPS = {lamp for s in R12 if s.camera_demonstrable for lamp in s.lamps}

# Share of cars with each fault. The rest are clean. Not the design's numbers (it gives none):
# chosen so a 40-car shift sees every kind a few times.
FAULTS = {"dead_headlight": 0.06, "dead_brake_lamp": 0.06, "wrong_colour": 0.04, "missing_badge": 0.04,
          "scratch": 0.08, "dent": 0.05, "panel_gap": 0.04}


@dataclass
class ShiftConfig:
    cars: int = 40
    seed: int = 7
    line_interval_s: float = 120.0          # one car off the line every 2 min: 30 per hour
    cars_per_truck: int = 6                 # the yard has 8 slots, so a truck's load must fit beside the next
    # A truck is due this long after its last car leaves the line. At 30 cars an hour the 8-slot yard
    # holds cars for about 8 x 2 = 16 minutes, so trucks due 20 minutes later overflow it.
    truck_after_last_car_min: float = 10.0
    sweeper_every_min: float = 30.0         # after the last scheduled truck, one more while cars remain
    late_truck_min: float = 40.0            # the second truck runs this late
    stuck_rate: float = 0.08                # cars that stop for something in the lane
    supervisor_delay_s: float = 45.0        # how long a person takes to look at a card
    truck_max_wait_min: float = 15.0
    miss_rate: float = 0.0                  # gate defect findings dropped, as a weaker model would
    miss_by_kind: dict = field(default_factory=dict)   # per defect type; overrides miss_rate for that type
    limit_h: float = 8.0
    step_s: float = 5.0


class MemoryHistory:
    """Every put, with the simulated time: first verdicts, routine times and turnarounds come from it."""

    def __init__(self, clock):
        self.clock, self.rows = clock, []

    def append(self, kind: str, key: str, payload: dict) -> bool:
        self.rows.append((self.clock(), kind, key, payload))
        return True

    def latest(self, kind: str, exclude_key: str | None = None) -> dict | None:
        return None

    def of(self, kind: str, key: str | None = None) -> list[tuple[datetime, dict]]:
        return [(t, p) for t, k, kk, p in self.rows if k == kind and (key is None or kk == key)]


def make_car(i: int, truck_id: str, rng: random.Random) -> tuple[Car, CarTruth, str]:
    colour = rng.choice(["blue", "white", "grey", "black"])
    sheet = BuildSheet(model="sedan", colour=colour, options=["rear_badge"], truck_id=truck_id)
    truth = CarTruth(colour=colour, fitted=["rear_badge"])
    r, fault = rng.random(), "clean"
    for name, share in FAULTS.items():
        if r < share:
            fault = name
            break
        r -= share
    if fault == "dead_headlight":
        truth.dead_lamps = {"headlight_left"}
    elif fault == "dead_brake_lamp":
        truth.dead_lamps = {"brake_left"}
    elif fault == "wrong_colour":
        truth.colour = "red"
    elif fault == "missing_badge":
        truth.fitted = []
    elif fault == "scratch":
        truth.defects = [Defect(rng.choice(["rear_left_door", "front_right_door", "bonnet"]), "scratch",
                                round(rng.uniform(3, 9), 1))]
    elif fault == "dent":
        truth.defects = [Defect(rng.choice(["rear", "front_left_door"]), "dent", round(rng.uniform(2, 6), 1))]
    elif fault == "panel_gap":
        truth.defects = [Defect("front_left_door", "panel_gap", None)]
    return Car(vin=f"VIN-S{i:03d}", build_sheet=sheet), truth, fault


def is_faulty(car: Car, truth: CarTruth) -> bool:
    s = car.build_sheet
    return bool(truth.defects or truth.dead_lamps & CHECKED_LAMPS or truth.colour != s.colour
                or truth.model != s.model or set(s.options) - set(truth.fitted))


def with_misses(llm, rate: float, by_kind: dict, rng: random.Random):
    """Drop a share of the gate's defect findings, as a model with that miss rate would."""
    if rate <= 0 and not by_kind:
        return llm
    original = llm.handlers["visual.defects"]

    def defects(call: LLMCall) -> dict:
        out = original(call)
        out["findings"] = [f for f in out["findings"] if rng.random() >= by_kind.get(f["type"], rate)]
        return out
    llm.on("visual.defects", defects)
    return llm


def _q(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(p * len(values)))], 1)


def _stats(values: list[float]) -> dict:
    return {"n": len(values), "median": round(statistics.median(values), 1) if values else None,
            "p90": _q(values, 0.9), "max": round(max(values), 1) if values else None}


@dataclass
class Shift:
    cfg: ShiftConfig
    app: MarshalApp = field(init=False)
    truths: dict[str, CarTruth] = field(default_factory=dict)       # as each car left the line
    faults: dict[str, str] = field(default_factory=dict)
    stuck: dict[str, dict] = field(default_factory=dict)        # vin -> scene, for cars that will stop
    seen: dict[str, float] = field(default_factory=dict)        # proposal or case id -> first seen (s)
    at_bay: dict[str, tuple[str, float]] = field(default_factory=dict)
    trucks: dict[str, dict] = field(default_factory=dict)
    waiting_samples: list[int] = field(default_factory=list)
    t: float = 0.0

    def __post_init__(self):
        cfg, rng = self.cfg, random.Random(self.cfg.seed)
        self.rng = rng
        rt = build_runtime(Settings(bus="memory", store="memory", history="none", artifacts="none",
                                    driving="fake", llm="offline", lab="sim"),
                           llm=with_misses(offline_llm(), cfg.miss_rate, cfg.miss_by_kind, random.Random(cfg.seed + 1)))
        rt.clock = SimClock()
        self.history = MemoryHistory(rt.clock)
        rt.store = WithHistory(rt.store, self.history)
        self.rt = rt
        self.app = MarshalApp(rt)
        self.start = rt.clock()
        n_trucks = -(-cfg.cars // cfg.cars_per_truck)
        for k in range(n_trucks):
            tid = f"T-{k + 1:02d}"
            last_car_s = (min(cfg.cars, (k + 1) * cfg.cars_per_truck) - 1) * cfg.line_interval_s
            sched = self.start + timedelta(seconds=last_car_s, minutes=cfg.truck_after_last_car_min)
            rt.plant.trucks[tid] = Truck(tid, sched, sched)
            self.trucks[tid] = {"scheduled": sched, "eta": sched, "departed": None, "loaded": [], "left_behind": []}
        self.queue = []
        for i in range(cfg.cars):
            tid = f"T-{i // cfg.cars_per_truck + 1:02d}"
            car, truth, fault = make_car(i + 1, tid, rng)
            self.queue.append((car, truth))
            self.faults[car.vin] = fault
            if rng.random() < cfg.stuck_rate:
                people = rng.random() < 0.5
                self.stuck[car.vin] = {"object": "cardboard box", "where": "in lane on the way to the yard",
                                       "distance_m": 3, "people": [{"role": "worker", "distance_m": 2}] if people else []}

    def now_s(self) -> float:
        return (self.rt.clock() - self.start).total_seconds()

    # ---- the plant around the flows ----

    def _line(self) -> None:
        while self.queue and self.now_s() >= self.cfg.line_interval_s * (self.cfg.cars - len(self.queue)):
            car, truth = self.queue.pop(0)
            self.rt.store.put(car.model_copy(update={"updated_at": self.rt.clock()}))
            self.rt.world.add(car.vin, truth)
            self.truths[car.vin] = copy.deepcopy(truth)     # the world's copy is changed by repairs
            self.rt.driving.spawn(car.vin, "line_end")

    def _late_truck(self) -> None:
        tid = "T-02"
        if tid in self.rt.plant.trucks and self.trucks[tid]["eta"] == self.trucks[tid]["scheduled"] \
                and self.rt.clock() >= self.trucks[tid]["scheduled"] - timedelta(minutes=15):
            truck = self.rt.plant.trucks[tid]
            truck.eta = truck.scheduled + timedelta(minutes=self.cfg.late_truck_min)
            self.trucks[tid]["eta"] = truck.eta

    async def _stuck_cars(self) -> None:
        for vin, scene in list(self.stuck.items()):
            car = self.rt.store.get(Car, vin)
            fake = self.rt.driving.cars.get(vin)
            # on its way to a yard slot, part way along: where a box in the lane would stop it
            if car is None or fake is None or not car.yard_slot or car.state == CarState.YARD \
                    or not fake.path or fake.travelled < 5:
                continue
            case_id = f"AC-{vin}-1"
            self.rt.world.scenes[case_id] = scene
            del self.stuck[vin]
            await self.rt.driving.raise_assist(vin, case_id, "unknown object in lane", OPTIONS)

    async def _supervisor(self) -> None:
        delay = self.cfg.supervisor_delay_s
        for p in list(self.rt.pending()):
            first = self.seen.setdefault(p.proposal_id, self.now_s())
            if self.now_s() - first < delay:
                continue
            if p.kind == "rework":
                await self.app.router.decide(p.proposal_id, True, SUPERVISOR)
            elif p.kind == "review":
                truth = self.truths[p.payload["vin"]]
                # the person looks at the car: a real defect is confirmed, nothing there is cleared
                await self.app.visual.decide_review(p.proposal_id, bool(truth.defects), SUPERVISOR)
            elif p.kind == "replan":
                await self.app.yard.decide(p.proposal_id, True, SUPERVISOR)
        for case in self.app.assistance.open_cases():
            first = self.seen.setdefault(case.case_id, self.now_s())
            if self.now_s() - first >= delay:
                await self.app.assistance.decide(case.case_id, case.recommended or OPTIONS[0], SUPERVISOR)
                self.rt.world.scenes.pop(case.case_id, None)

    async def _bays(self) -> None:
        for vin, plan in list(self.app.router.plans.items()):
            if plan.next_index >= len(plan.tickets):
                continue
            ticket = plan.tickets[plan.next_index]
            car = self.rt.store.get(Car, vin)
            if car.location != ticket.bay or car.mission_id:
                self.at_bay.pop(vin, None)
                continue
            bay, since = self.at_bay.setdefault(vin, (ticket.bay, self.now_s()))
            minutes = next((b["minutes"] for b in BAYS.values() if b["point"] == ticket.bay), None) or 30
            if self.now_s() - since >= minutes * 60:
                self.at_bay.pop(vin)
                await self.app.router.repair_done(vin, "bay")

    def _trucks(self) -> None:
        for tid, tr in list(self.trucks.items()):
            if tr["departed"] is not None or self.rt.clock() < tr["eta"]:
                continue
            mine = [c for c in self.rt.store.list(Car) if c.build_sheet.truck_id == tid]
            # it waits only for cars already passed and on their way to the yard; a car still in
            # inspection or rework goes on a later truck
            on_the_way = [c for c in mine if c.state != CarState.YARD and c.yard_slot and not c.do_not_ship
                          and (r := self.rt.store.get(CarReport, c.vin)) and r.verdict == Verdict.PASS]
            ready = not on_the_way
            waited = (self.rt.clock() - tr["eta"]) / timedelta(minutes=1)
            if ready or waited >= self.cfg.truck_max_wait_min:
                tr["loaded"] = self.app.yard.ship(tid)
                tr["departed"] = self.rt.clock()
                tr["left_behind"] = [c.vin for c in mine if c.vin not in tr["loaded"]]
                # as a plant would: a car the truck could not wait for is booked on the next one
                later = [t for t, x in self.trucks.items() if x["departed"] is None]
                if tr["left_behind"] and not later:
                    nid = f"T-{len(self.trucks) + 1:02d}"
                    sched = self.rt.clock() + timedelta(minutes=self.cfg.sweeper_every_min)
                    self.rt.plant.trucks[nid] = Truck(nid, sched, sched)
                    self.trucks[nid] = {"scheduled": sched, "eta": sched, "departed": None, "loaded": [],
                                        "left_behind": [], "extra": True}
                    later = [nid]
                for vin in tr["left_behind"]:
                    if later:
                        car = self.rt.store.get(Car, vin)
                        self.rt.store.put(car.model_copy(update={
                            "build_sheet": car.build_sheet.model_copy(update={"truck_id": later[0]})}))

    async def run(self) -> dict:
        step = self.cfg.step_s
        while self.now_s() < self.cfg.limit_h * 3600:
            self._line()
            self._late_truck()
            await self._stuck_cars()
            await self._supervisor()
            await self._bays()
            self._trucks()
            if int(self.now_s()) % 60 == 0:
                self.waiting_samples.append(self.app.yard.kpis()["cars_waiting_at_line_end"])
            if all(tr["departed"] for tr in self.trucks.values()):
                break
            await self.app.run_for(step)
        return self.kpis()

    # ---- the design's KPIs ----

    def kpis(self) -> dict:
        rt, h = self.rt, self.history
        cars = {c.vin: c for c in rt.store.list(Car)}
        hours = self.now_s() / 3600
        shipped = [v for v, c in cars.items() if c.state == CarState.SHIPPED]

        def first(kind: str, key: str):
            rows = h.of(kind, key)
            return rows[0] if rows else (None, None)

        # flow 1
        yard_times = {}
        for vin in cars:
            released = next((t for t, p in h.of("Car", vin) if p.get("released_at")), None)
            in_yard = next((t for t, p in h.of("Car", vin) if p["state"] == "yard"), None)
            if released and in_yard:
                yard_times[vin] = (in_yard - released).total_seconds() / 60
        clean = [m for v, m in yard_times.items() if self.faults[v] == "clean" and v not in self._stuck_vins()]
        reworked = [m for v, m in yard_times.items() if self._reworked(v)]
        flow1 = {
            "cars_per_hour_to_yard": round(len(yard_times) / hours, 1) if hours else None,
            "line_to_yard_min_clean": _stats(clean),
            "line_to_yard_min_after_rework": _stats(reworked),
            "cars_waiting_at_line_end": {"mean": round(statistics.mean(self.waiting_samples), 2) if self.waiting_samples else None,
                                         "max": max(self.waiting_samples, default=None)},
            "truck_loading_delay_min": {tid: round((tr["departed"] - tr["scheduled"]) / timedelta(minutes=1), 1)
                                        for tid, tr in self.trucks.items() if tr["departed"]},
            "cars_that_missed_their_truck": len({v for tr in self.trucks.values() for v in tr["left_behind"]}),
            "extra_trucks": sum(1 for tr in self.trucks.values() if tr.get("extra")),
            "exceptions": self._exceptions(),
        }
        # flow 2
        cases = rt.store.list(AssistanceCase)
        res = [(c.resolved_at - c.opened_at).total_seconds() for c in cases if c.resolved_at]
        flow2 = {"cases_per_shift": len(cases), "time_to_resolution_s": _stats(res),
                 "escalated": sum(c.escalated for c in cases),
                 "wait_recommended_with_people": sum(1 for c in cases if c.people_or_moving_equipment
                                                    and c.recommended and c.recommended.startswith("wait")),
                 "with_people": sum(1 for c in cases if c.people_or_moving_equipment),
                 "repeat_cases_per_location": self.app.assistance.kpis()["repeat_locations"],
                 "sent_to_test_lab": len(self.app.test_requests)}
        # flow 3: the first gate verdict of each car, against what is really wrong with it
        tp = fp = fn = tn = 0
        first_pass = review = 0
        gate_s = []
        for vin in cars:
            t, v = first("InspectionResult", f"{vin}:visual")
            if v is None:
                continue
            truth = self.truths[vin]
            visual_fault = bool(truth.defects or truth.colour != cars[vin].build_sheet.colour
                                or set(cars[vin].build_sheet.options) - set(truth.fitted))
            flagged = v["verdict"] != "PASS"
            first_pass += v["verdict"] == "PASS"
            review += v["verdict"] == "REVIEW"
            tp += flagged and visual_fault
            fp += flagged and not visual_fault
            fn += visual_fault and not flagged
            tn += not flagged and not visual_fault
        n_gate = tp + fp + fn + tn
        flow3 = {"first_pass_yield": round(first_pass / n_gate, 2) if n_gate else None,
                 "review_share": round(review / n_gate, 2) if n_gate else None,
                 "confirmed_defects": tp, "false_alarms": fp, "missed": fn}
        # flow 4
        routine = []
        for vin in cars:
            start = next((t for t, p in h.of("Car", vin) if p["state"] == "test_track"), None)
            t_res, _ = first("InspectionResult", f"{vin}:functional")
            if start and t_res:
                routine.append((t_res - start).total_seconds())
        # every routine run, re-inspections included: the store keeps only each car's latest
        failures, disagreements, seen_runs = {}, 0, set()
        for t, p in h.of("InspectionResult"):
            if p["kind"] != "functional" or (p["vin"], p["created_at"]) in seen_runs:
                continue
            seen_runs.add((p["vin"], p["created_at"]))
            for i in p["checklist"]:
                if i["telemetry_ok"] is False or i["camera_ok"] is False:
                    failures[i["name"]] = failures.get(i["name"], 0) + 1
                if i["telemetry_ok"] is not None and i["camera_ok"] is not None and i["telemetry_ok"] != i["camera_ok"]:
                    disagreements += 1
        lamp_faults = sum(1 for v in cars if self.truths[v].dead_lamps & CHECKED_LAMPS)   # as built
        flow4 = {"routine_time_per_car_s": _stats(routine), "failures_per_item": failures,
                 "telemetry_camera_disagreements": disagreements,
                 "cars_with_a_dead_checked_lamp": lamp_faults}
        # flow 5
        rework_cars = [v for v in cars if self._reworked(v)]
        turnaround = []
        for vin in rework_cars:
            fail = next((t for t, p in h.of("CarReport", vin) if p["verdict"] == "FAIL"), None)
            passed = next((t for t, p in h.of("CarReport", vin) if p["verdict"] == "PASS" and fail and t > fail), None)
            if fail and passed:
                turnaround.append((passed - fail).total_seconds() / 60)
        rk = self.app.router.kpis()
        escapes = [v for v in shipped if is_faulty(cars[v], self.rt.world.cars[v])]   # as shipped
        false_rework = [v for v in rework_cars if self.faults[v] == "clean"]
        flow5 = {"rework_rate": round(len(rework_cars) / len(cars), 2) if cars else None,
                 "rework_by_defect_type": rk["rework_by_defect_type"],
                 "rework_turnaround_min": _stats(turnaround),
                 "shipped_with_open_finding": rk["shipped_with_open_finding"],
                 "shipped_with_a_real_fault": escapes,
                 "clean_cars_sent_to_rework": false_rework}
        faults = {}
        for v, f in self.faults.items():
            faults[f] = faults.get(f, 0) + 1
        return {"config": {k: v for k, v in self.cfg.__dict__.items()},
                "shift_h": round(hours, 2), "cars": len(cars), "shipped": len(shipped),
                "faults": faults, "flow1_yard": flow1, "flow2_assistance": flow2, "flow3_gate": flow3,
                "flow4_track": flow4, "flow5_report": flow5}

    def _exceptions(self) -> dict[str, dict[str, int]]:
        """Yard exceptions by kind and what became of them: executed by rule, or proposed to a person."""
        out: dict[str, dict[str, int]] = {}
        for d in self.app.yard.decisions:
            out.setdefault(d["kind"], {})
            out[d["kind"]][d["status"]] = out[d["kind"]].get(d["status"], 0) + 1
        return out

    def _reworked(self, vin: str) -> bool:
        return any(p.get("rework") for _, p in self.history.of("CarReport", vin))

    def _stuck_vins(self) -> set[str]:
        return {c.vin for c in self.rt.store.list(AssistanceCase)}


def summary(k: dict) -> str:
    f1, f2, f3, f4, f5 = (k["flow1_yard"], k["flow2_assistance"], k["flow3_gate"], k["flow4_track"],
                          k["flow5_report"])
    lines = [
        f"shift {k['shift_h']} h, {k['cars']} cars, {k['shipped']} shipped; faults {k['faults']}",
        "",
        "flow 1 yard",
        f"  cars per hour to the yard       {f1['cars_per_hour_to_yard']}",
        f"  line to yard, clean (min)       {f1['line_to_yard_min_clean']}",
        f"  line to yard, after rework      {f1['line_to_yard_min_after_rework']}",
        f"  cars waiting at line end        {f1['cars_waiting_at_line_end']}",
        f"  truck loading delay (min)       {f1['truck_loading_delay_min']}",
        f"  cars that missed their truck    {f1['cars_that_missed_their_truck']} (extra trucks {f1['extra_trucks']})",
        f"  yard exceptions                 {f1['exceptions']}",
        "flow 2 assistance",
        f"  cases per shift                 {f2['cases_per_shift']} ({f2['with_people']} with people, wait recommended {f2['wait_recommended_with_people']})",
        f"  time to resolution (s)          {f2['time_to_resolution_s']}",
        f"  escalated after 60 s            {f2['escalated']}",
        f"  repeat cases per location       {f2['repeat_cases_per_location']}",
        f"  sent to the test lab            {f2['sent_to_test_lab']}",
        "flow 3 gate (first verdict vs the car's real faults)",
        f"  first-pass yield                {f3['first_pass_yield']}",
        f"  review share                    {f3['review_share']}",
        f"  confirmed / false alarms / missed  {f3['confirmed_defects']} / {f3['false_alarms']} / {f3['missed']}",
        "flow 4 track",
        f"  routine time per car (s)        {f4['routine_time_per_car_s']}",
        f"  failures per item               {f4['failures_per_item']}",
        f"  telemetry/camera disagreements  {f4['telemetry_camera_disagreements']} (cars with a dead checked lamp: {f4['cars_with_a_dead_checked_lamp']})",
        "flow 5 report and rework",
        f"  rework rate                     {f5['rework_rate']}",
        f"  rework by defect type           {f5['rework_by_defect_type']}",
        f"  rework turnaround (min)         {f5['rework_turnaround_min']}",
        f"  shipped with open finding       {f5['shipped_with_open_finding']} (target 0)",
        f"  shipped with a real fault       {len(f5['shipped_with_a_real_fault'])} {f5['shipped_with_a_real_fault']}",
        f"  clean cars sent to rework       {len(f5['clean_cars_sent_to_rework'])}",
    ]
    return "\n".join(lines)


def recall_misses(path: Path) -> tuple[float, dict]:
    """Miss rates from a v2-damage eval: 1 - recall, per kind and overall."""
    m = json.loads(path.read_text())["flows"]["damage"]["metrics"]
    return round(1 - m["damage_recall"], 3), {k: round(1 - v, 3) for k, v in m["recall_by_kind"].items()}


HEADLINE = [("flow1_yard", "cars_per_hour_to_yard"), ("flow3_gate", "missed"), ("flow3_gate", "false_alarms"),
            ("flow4_track", "telemetry_camera_disagreements"), ("flow5_report", "rework_rate"),
            ("flow5_report", "shipped_with_open_finding")]


def sweep(results: list[dict]) -> dict:
    """Headline KPIs over several seeds: totals for counts, means for rates."""
    out = {"seeds": len(results), "cars": sum(k["cars"] for k in results),
           "cars_with_a_visual_defect": sum(k["faults"].get(f, 0) for k in results
                                            for f in ("scratch", "dent", "panel_gap")),
           "shipped_with_a_real_fault": sum(len(k["flow5_report"]["shipped_with_a_real_fault"]) for k in results)}
    for flow, name in HEADLINE:
        vals = [k[flow][name] for k in results if k[flow][name] is not None]
        out[name] = round(statistics.mean(vals), 2) if name.endswith(("rate", "hour_to_yard")) else sum(vals)
    out["line_to_yard_min_clean_median"] = round(statistics.median(
        [k["flow1_yard"]["line_to_yard_min_clean"]["median"] for k in results
         if k["flow1_yard"]["line_to_yard_min_clean"]["median"] is not None]), 1)
    out["rework_turnaround_min_median"] = round(statistics.median(
        [k["flow5_report"]["rework_turnaround_min"]["median"] for k in results
         if k["flow5_report"]["rework_turnaround_min"]["median"] is not None]), 1)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cars", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--miss-rate", type=float, default=0.0, help="share of gate defect findings dropped")
    ap.add_argument("--damage-recall", type=Path, default=None,
                    help="a v2-damage eval result: drop findings at its measured miss rate per kind")
    ap.add_argument("--seeds", type=int, default=1, help="run this many shifts (seed, seed+1, ...) and sum them")
    ap.add_argument("--stuck-rate", type=float, default=0.08)
    ap.add_argument("--supervisor-delay", type=float, default=45.0, help="seconds a person takes to decide")
    ap.add_argument("--out", type=Path, default=Path("eval/results"))
    args = ap.parse_args(argv)
    miss, by_kind = (recall_misses(args.damage_recall) if args.damage_recall else (args.miss_rate, {}))
    results = []
    for seed in range(args.seed, args.seed + args.seeds):
        cfg = ShiftConfig(cars=args.cars, seed=seed, miss_rate=miss, miss_by_kind=by_kind,
                          stuck_rate=args.stuck_rate, supervisor_delay_s=args.supervisor_delay)
        results.append(asyncio.run(Shift(cfg).run()))
    args.out.mkdir(parents=True, exist_ok=True)
    tag = "recall" if args.damage_recall else f"miss{miss}"
    out = args.out / f"shift-{args.cars}cars-x{args.seeds}-{tag}-{datetime.now():%Y%m%d-%H%M%S}.json"
    report = {"shifts": results, "sweep": sweep(results), "miss_rate": miss, "miss_by_kind": by_kind}
    out.write_text(json.dumps(report, indent=2, default=str))
    print(summary(results[0]))
    if args.seeds > 1:
        print(f"\nover {args.seeds} shifts: " + json.dumps(report["sweep"]))
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
