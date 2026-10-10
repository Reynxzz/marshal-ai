"""Flow 5: Inspection report and rework routing.

Merges visual and functional results into one report per car. A PASS goes to the
yard. Anything else gets a Gemini-written summary and a rework plan that a
supervisor confirms; after repair only the failed items are re-inspected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from marshal_ai.driving import MissionDone, MissionGo
from marshal_ai.fakeworld import GATE_VIEWS
from marshal_ai.flows.base import ask_or, log, update_car
from marshal_ai.llm import to_prompt_json
from marshal_ai.records import (
    Car, CarReport, CarState, ChecklistItem, InspectionResult, ReworkTicket, Verdict,
)

if TYPE_CHECKING:
    from marshal_ai.app import MarshalApp

BayKey = Literal["mechanical", "electrical", "body", "paint", "production_control"]
BAYS: dict[str, dict] = {
    "mechanical": {"point": "rework.mechanical", "handles": "brakes, steering", "minutes": 45},
    "electrical": {"point": "rework.electrical", "handles": "lights, indicators, electrical", "minutes": 25},
    "body": {"point": "rework.body", "handles": "panel gap, trim, missing badge", "minutes": 30},
    "paint": {"point": "rework.paint", "handles": "paint scratch, small dent", "minutes": 20},
    "production_control": {"point": "production_control", "handles": "wrong variant", "minutes": None},
}
SAFETY_ORDER = ["mechanical", "electrical", "body", "paint", "production_control"]
STEP_DEFECT = {
    "headlights": ("headlight", "electrical"), "indicators": ("indicator", "electrical"),
    "brake_lights": ("brake_light", "electrical"), "reverse": ("reverse_light", "electrical"),
    "brake_test": ("brakes", "mechanical"), "full_lock": ("steering", "mechanical"),
}
# The design's bay table, by the words a defect is described with. Checked in this order, so a
# light wins over a word it shares with paint ("headlight_haze"). It used to be an exact lookup on
# the finding's type, but Gemini writes free text: in the evaluation answers 13% of findings
# ("scratches", "scuff", "paint_damage", "chip", "corrosion", "headlight_haze") missed the table
# and fell to the body bay.
BAY_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("electrical", ("light", "lamp", "indicator", "blinker", "electric", "wiring")),
    ("mechanical", ("brake", "steering")),
    ("paint", ("scratch", "scuff", "scrape", "abrasion", "dent", "ding", "paint", "chip", "flak",
               "peel", "corros", "rust", "clear coat", "clearcoat")),
]


def bay_for(defect_type: str) -> BayKey:
    """The rework bay for a defect, from the design's table; panel gaps, trim, missing or broken
    parts and anything else unrecognised go to the body bay, where a person looks at it."""
    text = defect_type.lower().replace("_", " ")
    return next((bay for bay, words in BAY_WORDS if any(w in text for w in words)), "body")

BADGE_ITEMS = {"rear_badge", "front_badge", "badge"}


class ReworkStepDraft(BaseModel):
    bay: BayKey
    defect_type: str
    reason: str = Field(description="what is wrong and the evidence, one sentence")


class ReportDraft(BaseModel):
    summary: str = Field(description="two or three plain sentences for quality staff")
    steps: list[ReworkStepDraft] = Field(description="one per defect, safety items first")


SYSTEM = """You write one-page inspection reports for finished cars and plan rework. Summarise what
failed and the evidence in plain language. Plan one rework step per defect, choosing the bay from the
list given, with safety items (brakes, steering, lights) first. Wrong variants go to production_control."""


def required_steps(visual: InspectionResult | None, functional: InspectionResult | None) -> list[ReworkStepDraft]:
    """Rule-based rework steps, used to check and complete Gemini's plan."""
    steps: list[ReworkStepDraft] = []
    if visual and visual.verdict != Verdict.PASS:
        for f in visual.findings:
            steps.append(ReworkStepDraft(bay=bay_for(f.type), defect_type=f.type,
                                         reason=f"{f.type} on {f.zone}"))
        for m in visual.build_sheet_mismatches:
            item = m.split(":", 1)[0].strip()
            if item in BADGE_ITEMS:
                steps.append(ReworkStepDraft(bay="body", defect_type="missing_badge", reason=m))
            else:
                steps.append(ReworkStepDraft(bay="production_control", defect_type="wrong_variant", reason=m))
    if functional:
        for item in functional.checklist:
            if not item.passed:
                defect, bay = STEP_DEFECT.get(item.name, (item.name, "electrical"))
                steps.append(ReworkStepDraft(bay=bay, defect_type=defect,
                                             reason=f"{item.name}: telemetry {item.telemetry_ok}, camera {item.camera_ok}"))
    return steps


def complete_plan(draft: list[ReworkStepDraft], required: list[ReworkStepDraft]) -> list[ReworkStepDraft]:
    """Keep Gemini's steps, add any defect it missed, one visit per bay, safety first."""
    by_bay: dict[str, ReworkStepDraft] = {}
    for s in draft + required:
        if s.bay in by_bay:
            if s.defect_type not in by_bay[s.bay].defect_type:
                prev = by_bay[s.bay]
                by_bay[s.bay] = prev.model_copy(update={
                    "defect_type": f"{prev.defect_type}, {s.defect_type}",
                    "reason": f"{prev.reason}; {s.reason}"})
        else:
            by_bay[s.bay] = s
    required_bays = {s.bay for s in required}
    kept = [s for s in by_bay.values() if s.bay in required_bays]
    return sorted(kept, key=lambda s: SAFETY_ORDER.index(s.bay))


@dataclass
class _Plan:
    tickets: list[ReworkTicket]
    visual_views: list[str] | None = None      # None: no visual re-inspection
    check_variant: bool = False
    functional_steps: list[int] = field(default_factory=list)
    next_index: int = 0


class ReportRouter:
    def __init__(self, app: "MarshalApp"):
        self.app = app
        self.rt = app.rt
        self.plans: dict[str, _Plan] = {}
        self.waiting_for_bay: list[str] = []

    # ---------- results in ----------

    async def on_result(self, result: InspectionResult) -> None:
        if result.kind == "visual":
            if result.verdict == Verdict.PASS:
                await self.after_visual_pass(result.vin)
            elif result.verdict == Verdict.FAIL:
                await self.finalize(result.vin)
            # REVIEW waits for the supervisor (flow 3 review card)
        else:
            await self.finalize(result.vin)

    async def after_visual_pass(self, vin: str) -> None:
        plan = self.plans.get(vin)
        functional = self.rt.store.get(InspectionResult, f"{vin}:functional")
        if plan and plan.functional_steps:
            await self.app.functional.start(vin, plan.functional_steps)
        elif functional is not None and functional.verdict == Verdict.PASS:
            await self.finalize(vin)
        else:
            await self.app.functional.start(vin)

    def views_for(self, vin: str) -> tuple[list[str] | None, bool]:
        """Gate views and whether to re-check the build sheet, for the car's next gate pass."""
        plan = self.plans.get(vin)
        if plan is None or plan.visual_views is None:
            return None, True
        return plan.visual_views, plan.check_variant

    # ---------- report ----------

    def merge_verdict(self, visual: InspectionResult | None, functional: InspectionResult | None) -> Verdict:
        verdicts = {r.verdict for r in (visual, functional) if r is not None}
        if Verdict.FAIL in verdicts:
            return Verdict.FAIL
        if visual is None or functional is None or Verdict.REVIEW in verdicts:
            return Verdict.REVIEW
        return Verdict.PASS

    async def finalize(self, vin: str) -> CarReport:
        visual = self.rt.store.get(InspectionResult, f"{vin}:visual")
        functional = self.rt.store.get(InspectionResult, f"{vin}:functional")
        verdict = self.merge_verdict(visual, functional)
        existing = self.rt.store.get(CarReport, vin)
        plan = self.plans.get(vin)
        reinspection = list(existing.reinspection) if existing else []
        if plan is not None:           # this result is a re-inspection after rework
            reinspection += [r for r in (visual, functional) if r is not None]
        failed = [i for i in (functional.checklist if functional else []) if not i.passed]
        report = CarReport(vin=vin, verdict=verdict, findings=visual.findings if visual else [],
                           failed_checks=failed, rework=existing.rework if existing else [],
                           reinspection=reinspection, updated_at=self.rt.clock())

        if verdict == Verdict.PASS:
            report.summary = (existing.summary + " Re-inspection passed." if existing and plan
                              else "All visual and functional checks passed.")
            self.rt.store.put(report)
            self.plans.pop(vin, None)
            await self.app.yard.send_to_slot(vin)
            return report

        required = required_steps(visual, functional)
        prompt = (f"Car {vin}. Build sheet: {to_prompt_json(self.rt.store.get(Car, vin).build_sheet)}\n"
                  f"Visual result: {to_prompt_json(visual) if visual else 'none'}\n"
                  f"Functional result: {to_prompt_json(functional) if functional else 'none'}\n"
                  f"Rework bays: {to_prompt_json(BAYS)}")
        fallback = ReportDraft(
            summary=f"{vin} failed inspection: " + "; ".join(f"{s.bay}: {s.reason}" for s in required)
                    + ". (Written without Gemini: model unavailable.)",
            steps=required)
        draft, _ = await ask_or(self.rt, "report.write", prompt, ReportDraft, fallback, system=SYSTEM)
        steps = complete_plan(draft.steps, required)
        report.summary = draft.summary
        self.rt.store.put(report)
        update_car(self.rt, vin, state=CarState.REPORT)
        self.rt.propose("rework", f"Rework for {vin}: " + ", then ".join(s.bay for s in steps),
                        {"vin": vin, "steps": [s.model_dump() for s in steps],
                         "visual": visual.model_dump(mode="json") if visual else None,
                         "failed_steps": [i.step for i in failed]})
        return report

    # ---------- supervisor decision and rework ----------

    async def decide(self, proposal_id: str, confirm: bool, by: str) -> dict:
        p = self.rt.decide(proposal_id, "rework", confirm, by, what="rework proposal")
        vin = p.payload["vin"]
        report = self.rt.store.get(CarReport, vin)
        if not confirm:                 # supervisor clears the findings
            report = report.model_copy(update={"verdict": Verdict.PASS,
                                               "summary": report.summary + f" Findings cleared by {by}."})
            self.rt.store.put(report)
            await self.app.yard.send_to_slot(vin)
            return {"vin": vin, "status": "cleared"}

        steps = [ReworkStepDraft.model_validate(s) for s in p.payload["steps"]]
        tickets = [ReworkTicket(bay=BAYS[s.bay]["point"], defect_type=s.defect_type, reason=s.reason,
                                confirmed_by=by) for s in steps]
        visual = p.payload.get("visual")
        views = None
        check_variant = False
        if visual and visual["verdict"] != "PASS":
            zones = {f["zone"] for f in visual["findings"]}
            views = [v for v, zs in GATE_VIEWS.items() if zones & set(zs)]
            check_variant = bool(visual["build_sheet_mismatches"])
        self.plans[vin] = _Plan(tickets=tickets, visual_views=views, check_variant=check_variant,
                                functional_steps=p.payload["failed_steps"])
        self.rt.store.put(report.model_copy(update={"rework": tickets}))
        await self.dispatch(vin)
        return {"vin": vin, "status": "confirmed", "bays": [t.bay for t in tickets]}

    async def dispatch(self, vin: str) -> str | None:
        """Send the car to its next bay, or hand it to Yard Traffic when the bay is full."""
        plan = self.plans[vin]
        if not plan.tickets:
            # A confirmed rework with no steps means the report was not actually a failure:
            # merge_verdict returns REVIEW when a result is missing, which sent a car with no
            # failed checks down this path and crashed on tickets[0].
            raise ValueError(f"{vin}: rework was confirmed with no bays to visit; "
                             "the report had no failed checks")
        ticket = plan.tickets[plan.next_index]
        if ticket.bay in self.rt.plant.bay_capacity and not self.rt.plant.bay_free(ticket.bay):
            if vin not in self.waiting_for_bay:
                self.waiting_for_bay.append(vin)
                car = self.rt.store.get(Car, vin)
                update_car(self.rt, vin, flags=car.flags + ["rework_waiting"], do_not_ship=True)
                await self.app.yard.handle_exception(
                    "rework_bay_full", {"vin": vin, "bay": ticket.bay,
                                        "occupants": self.rt.plant.bay_occupants[ticket.bay]},
                    key=f"rework_bay_full:{vin}:{ticket.bay}")
            return None
        if ticket.bay in self.rt.plant.bay_capacity:
            self.rt.plant.occupy(ticket.bay, vin)
        mission = MissionGo(mission_id=self.rt.next_id("M", 4), vin=vin, waypoints=[ticket.bay])
        await self.rt.missions.send(mission)
        car = self.rt.store.get(Car, vin)
        update_car(self.rt, vin, state=CarState.REWORK, mission_id=mission.mission_id,
                   flags=[f for f in car.flags if f != "rework_waiting"])
        return ticket.bay

    async def repair_done(self, vin: str, by: str) -> dict:
        """The bay reports the repair finished: next bay, or re-inspect the failed items only."""
        plan = self.plans[vin]
        ticket = plan.tickets[plan.next_index]
        ticket.done_at = self.rt.clock()
        types = [t.strip() for t in ticket.defect_type.split(",")]
        repaired = types + (["electrical"] if "electrical" in ticket.bay else [])
        if self.rt.world is not None:          # simulation: the repair changes the car
            self.rt.world.repair(vin, repaired)
            if {"missing_badge", "wrong_variant"} & set(types):
                sheet = self.rt.store.get(Car, vin).build_sheet
                truth = self.rt.world.cars[vin]
                truth.fitted = list(sheet.options)
                if "wrong_variant" in types:   # production control brings the car to spec
                    truth.colour, truth.model, truth.trim = sheet.colour, sheet.model, sheet.trim
        elif hasattr(self.rt.driving, "repair"):  # CARLA: the bay fixes the car on the simulator
            self.rt.driving.repair(vin, repaired)
        self.rt.plant.release(vin)
        plan.next_index += 1
        report = self.rt.store.get(CarReport, vin)
        self.rt.store.put(report.model_copy(update={"rework": plan.tickets}))
        await self._serve_waiting()
        if plan.next_index < len(plan.tickets):
            return {"vin": vin, "next_bay": await self.dispatch(vin)}
        return {"vin": vin, "reinspect": await self.reinspect(vin)}

    async def reinspect(self, vin: str) -> str:
        plan = self.plans[vin]
        car = self.rt.store.get(Car, vin)
        update_car(self.rt, vin, do_not_ship=False, flags=[f for f in car.flags if f != "rework_waiting"])
        if plan.visual_views is not None or plan.check_variant:
            mission = MissionGo(mission_id=self.rt.next_id("M", 4), vin=vin,
                                waypoints=["inspection_gate"], max_speed_kmh=5.0)
            await self.rt.missions.send(mission)
            update_car(self.rt, vin, state=CarState.INSPECTION_GATE, mission_id=mission.mission_id)
            return "inspection_gate"
        await self.app.functional.start(vin, plan.functional_steps)
        return "test_track"

    async def _serve_waiting(self) -> None:
        for vin in list(self.waiting_for_bay):
            plan = self.plans[vin]
            if self.rt.plant.bay_free(plan.tickets[plan.next_index].bay):
                self.waiting_for_bay.remove(vin)
                await self.dispatch(vin)

    async def on_mission_done(self, event: MissionDone) -> None:
        if event.location.startswith("rework.") or event.location == "production_control":
            update_car(self.rt, event.vin, location=event.location, mission_id=None)
            log.info("rework: %s arrived at %s", event.vin, event.location)

    def kpis(self) -> dict:
        reports = self.rt.store.list(CarReport)
        by_type: dict[str, int] = {}
        for r in reports:
            for t in r.rework:
                by_type[t.defect_type] = by_type.get(t.defect_type, 0) + 1
        shipped_open = [c.vin for c in self.rt.store.list(Car)
                        if c.state == CarState.SHIPPED
                        and (r := self.rt.store.get(CarReport, c.vin)) and r.verdict != Verdict.PASS]
        return {"reports": len(reports), "rework_by_defect_type": by_type,
                "shipped_with_open_finding": len(shipped_open)}


__all__ = ["BAYS", "ChecklistItem", "ReportDraft", "ReportRouter", "ReworkStepDraft"]
