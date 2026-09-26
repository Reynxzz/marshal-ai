"""Flow 3: Drive-through visual inspection.

Four gate cameras; Gemini checks each view's body zones and, separately, compares the
car with its build sheet. Confidence decides pass, human review or fail.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from marshal_ai.events import Topic
from marshal_ai.fakeworld import GATE_VIEWS
from marshal_ai.flows.base import ask, update_car
from marshal_ai.llm import Media
from marshal_ai.records import Car, CarState, InspectionResult, Verdict, ZoneFinding

if TYPE_CHECKING:
    from marshal_ai.app import MarshalApp

PASS_CONFIDENCE = 0.9
REVIEW_CONFIDENCE = 0.6
BODY_ZONES = [z for zones in GATE_VIEWS.values() for z in zones]
ZONE_VIEW = {z: view for view, zones in GATE_VIEWS.items() for z in zones}


class Finding(BaseModel):
    zone: str
    type: str = Field(description="scratch, dent, panel_gap, missing_part, paint_run, ...")
    size_cm: float | None = None
    confidence: float = Field(ge=0, le=1)


class ViewAssessment(BaseModel):
    findings: list[Finding]
    clear_confidence: float = Field(ge=0, le=1, description="confidence that zones without findings are defect-free")


class Mismatch(BaseModel):
    item: str = Field(description="colour, model, trim, or a build sheet option such as roof_rails")
    expected: str
    observed: str
    confidence: float = Field(ge=0, le=1)


class VariantAssessment(BaseModel):
    observed_colour: str
    mismatches: list[Mismatch]
    confidence: float = Field(ge=0, le=1, description="confidence the car matches except for listed mismatches")


DEFECT_SYSTEM = """You inspect finished cars at a drive-through gate. For the zones visible in this
image, report every paint or body defect (scratch, dent, panel gap, missing part) with its zone,
type, approximate size and your confidence. Report nothing you cannot see."""

VARIANT_SYSTEM = """You check that a finished car matches its build sheet: colour, model, trim and
fitted options (e.g. roof rails, rear badge). Report each mismatch with expected, observed and confidence."""


def mismatch_text(m: Mismatch) -> str:
    return f"{m.item}: build sheet says {m.expected}, car shows {m.observed} ({m.confidence:.2f})"


def visual_verdict(findings: list[Finding | ZoneFinding], mismatches: list[Mismatch],
                   clear_confidences: list[float], variant_confidence: float = 1.0) -> Verdict:
    """Fail on anything over 0.9, review on anything 0.6 to 0.9 or any uncertain all-clear, else pass."""
    scores = [f.confidence for f in findings] + [m.confidence for m in mismatches]
    if any(c > PASS_CONFIDENCE for c in scores):
        return Verdict.FAIL
    if any(c >= REVIEW_CONFIDENCE for c in scores):
        return Verdict.REVIEW
    if any(c <= PASS_CONFIDENCE for c in clear_confidences) or variant_confidence <= PASS_CONFIDENCE:
        return Verdict.REVIEW
    return Verdict.PASS


class VisualInspector:
    def __init__(self, app: "MarshalApp"):
        self.app = app
        self.rt = app.rt

    async def _view(self, image: Media, zones: list[str]) -> ViewAssessment:
        prompt = f"Zones visible in this image: {zones}. Report defects in these zones only."
        return await ask(self.rt, "visual.defects", prompt, ViewAssessment, [image], DEFECT_SYSTEM)

    async def _variant(self, car: Car, images: list[Media]) -> VariantAssessment:
        prompt = f"Build sheet: {car.build_sheet.model_dump_json()}\nCompare the car in these images with it."
        return await ask(self.rt, "visual.variant", prompt, VariantAssessment, images, VARIANT_SYSTEM)

    async def inspect(self, vin: str, views: list[str] | None = None, check_variant: bool = True) -> InspectionResult:
        """Inspect a car at the gate. Re-inspection passes only the views that had findings."""
        car = self.rt.store.get(Car, vin)
        # Deliberately inline. Capturing off-thread, concurrently with a running driving loop,
        # segfaults the CARLA server; capturing inline blocks the loop so no ticks happen and
        # no frame arrives. Neither works, so a gate inspection must run in a phase with no
        # active driving loop - see the README and run_carla_day2.sh.
        images = self.rt.gate_camera.capture(vin, views)
        view_names = [m.label.split()[0] for m in images]
        jobs = [self._view(img, GATE_VIEWS[name]) for img, name in zip(images, view_names)]
        if check_variant:
            jobs.append(self._variant(car, self.rt.gate_camera.capture(vin)))
        results = await asyncio.gather(*jobs)
        view_results: list[ViewAssessment] = results[:len(images)]
        variant: VariantAssessment | None = results[len(images)] if check_variant else None

        findings: list[ZoneFinding] = []
        kept: list[Finding] = []
        for img, name, va in zip(images, view_names, view_results):
            for f in va.findings:
                if f.zone not in GATE_VIEWS[name] or f.confidence < REVIEW_CONFIDENCE:
                    continue
                kept.append(f)
                findings.append(ZoneFinding(zone=f.zone, type=f.type, size_cm=f.size_cm,
                                            confidence=f.confidence, image=img.uri))
        mismatches = [m for m in (variant.mismatches if variant else []) if m.confidence >= REVIEW_CONFIDENCE]
        verdict = visual_verdict(kept, mismatches, [v.clear_confidence for v in view_results],
                                 variant.confidence if variant else 1.0)
        result = InspectionResult(vin=vin, kind="visual", verdict=verdict, findings=findings,
                                  build_sheet_mismatches=[mismatch_text(m) for m in mismatches],
                                  created_at=self.rt.clock())
        self.rt.store.put(result)
        if verdict == Verdict.REVIEW:
            update_car(self.rt, vin, state=CarState.REVIEW, location="inspection_gate")
            self.rt.propose("review", f"Visual review for {vin}",
                            {"vin": vin, "findings": [f.model_dump() for f in findings],
                             "mismatches": result.build_sheet_mismatches})
        else:
            update_car(self.rt, vin, location="inspection_gate")
        await self.rt.bus.publish(Topic.INSPECTION_RESULTS, result)
        return result

    async def decide_review(self, proposal_id: str, confirm_defect: bool, by: str) -> InspectionResult:
        """Supervisor looks at the review card: confirm the defect (to rework) or clear it (to the track)."""
        p = self.rt.proposals[proposal_id]
        if p.kind != "review" or p.status != "pending":
            raise ValueError(f"{proposal_id} is not a pending visual review")
        p.status, p.decided_by = ("approved" if confirm_defect else "rejected"), by
        vin = p.payload["vin"]
        result = self.rt.store.get(InspectionResult, f"{vin}:visual")
        if confirm_defect:
            result = result.model_copy(update={"verdict": Verdict.FAIL})
        else:
            result = result.model_copy(update={"verdict": Verdict.PASS, "findings": [],
                                               "build_sheet_mismatches": []})
        self.rt.store.put(result)
        await self.rt.bus.publish(Topic.INSPECTION_RESULTS, result)
        return result

    def kpis(self) -> dict:
        results = [r for r in self.rt.store.list(InspectionResult) if r.kind == "visual"]
        n = len(results) or 1
        return {"inspected": len(results),
                "first_pass_yield": round(sum(r.verdict == Verdict.PASS for r in results) / n, 2),
                "review_share": round(sum(p.kind == "review" for p in self.rt.proposals.values()) / n, 2)}
