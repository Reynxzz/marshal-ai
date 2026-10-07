"""Flow 3: Drive-through visual inspection.

Four gate cameras; Gemini checks each view's body zones and, separately, compares the
car with its build sheet. Confidence decides pass, human review or fail.
"""

from __future__ import annotations

import logging

import asyncio
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from marshal_ai.events import Topic
from marshal_ai.fakeworld import GATE_VIEWS
from marshal_ai.flows.base import ask_or, update_car
from marshal_ai.llm import Media
from marshal_ai.records import (BuildSheet, Car, CarState, InspectionResult, Verdict,
                                 ZoneFinding)

if TYPE_CHECKING:
    from marshal_ai.app import MarshalApp

PASS_CONFIDENCE = 0.9
log = logging.getLogger(__name__)

REVIEW_CONFIDENCE = 0.6
BODY_ZONES = [z for zones in GATE_VIEWS.values() for z in zones]
ZONE_VIEW = {z: view for view, zones in GATE_VIEWS.items() for z in zones}


class Finding(BaseModel):
    zone: str
    type: str = Field(description="scratch, dent, panel_gap, missing_part, paint_run, ...")
    size_cm: float | None = None
    confidence: float = Field(ge=0, le=1)
    observation: str = Field(default="", description="what you see, one sentence")


class ViewAssessment(BaseModel):
    findings: list[Finding]
    clear_confidence: float = Field(ge=0, le=1, description="confidence that zones without findings are defect-free")
    observation: str = Field(default="", description="what this view shows, one sentence")


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

VARIANT_SYSTEM = """You check that a finished car matches its build sheet. Report a mismatch only for
a field the build sheet actually states: its colour, model, trim, or one of the options it lists.

The options list says what this car is built with. It is not a complete description of the car, so do
not treat something you can see but that is not listed as a mismatch - if the sheet does not mention
a sunroof, a badge or a spoiler, that is not a claim the car has none. Report each mismatch with the
build sheet's value as expected, what you see as observed, and your confidence."""


def declared_items(sheet: BuildSheet) -> set[str]:
    """The build sheet fields a mismatch may be reported against."""
    return {"colour", "model", "trim"} | {o.strip().lower() for o in sheet.options}


def mismatch_text(m: Mismatch) -> str:
    return f"{m.item}: build sheet says {m.expected}, car shows {m.observed} ({m.confidence:.2f})"


def visual_verdict(findings: list[Finding | ZoneFinding], mismatches: list[Mismatch],
                   clear_confidences: list[float], variant_confidence: float = 1.0) -> Verdict:
    """Fail on anything over 0.9, review on anything 0.6 to 0.9 or any uncertain all-clear, else pass.

    An all-clear counts as confident at the bar, not only above it. Requiring more than 0.9 made
    PASS practically unreachable: on real gate frames a clean car scored [0.9, 0.95, 0.9, 0.9] and
    still went to review, because 0.9 is the value the model returns most often. A gate that sends
    every car to a human is not a gate. The bar itself is unchanged - anything below 0.9 is still
    too uncertain to clear a car on.
    """
    scores = [f.confidence for f in findings] + [m.confidence for m in mismatches]
    if any(c > PASS_CONFIDENCE for c in scores):
        return Verdict.FAIL
    if any(c >= REVIEW_CONFIDENCE for c in scores):
        return Verdict.REVIEW
    if any(c < PASS_CONFIDENCE for c in clear_confidences) or variant_confidence < PASS_CONFIDENCE:
        return Verdict.REVIEW
    return Verdict.PASS


class VisualInspector:
    def __init__(self, app: "MarshalApp"):
        self.app = app
        self.rt = app.rt

    async def _view(self, image: Media, zones: list[str]) -> ViewAssessment:
        prompt = f"Zones visible in this image: {zones}. Report defects in these zones only."
        # no answer: nothing is known to be clear, so the verdict becomes REVIEW for a person
        view, _ = await ask_or(self.rt, "visual.defects", prompt, ViewAssessment,
                               ViewAssessment(findings=[], clear_confidence=0.0), [image], DEFECT_SYSTEM)
        return view

    async def _variant(self, car: Car, images: list[Media]) -> VariantAssessment:
        declared = declared_items(car.build_sheet)
        prompt = (f"Build sheet: {car.build_sheet.model_dump_json()}\n"
                  f"The only fields it states are: {', '.join(sorted(declared))}.\n"
                  "Compare the car in these images with it.")
        # no answer: nothing is known to match, so the variant check adds no mismatches
        seen, _ = await ask_or(self.rt, "visual.variant", prompt, VariantAssessment,
                               VariantAssessment(observed_colour="not checked", mismatches=[], confidence=0.0),
                               images, VARIANT_SYSTEM)
        # Drop anything the build sheet never claimed. Left open, this check failed a healthy car
        # twice over, each time on a different invention: "sunroof: build sheet says not present,
        # car shows present" and "rear_badge: build sheet says none, car shows TESLA" - the latter
        # at confidence 1.00, on a Lincoln. An option the sheet omits is not an option it forbids,
        # so a mismatch has to name something the sheet actually states.
        kept, dropped = [], []
        for m in seen.mismatches:
            (kept if m.item.strip().lower() in declared else dropped).append(m)
        if dropped:
            log.info("visual: %s ignoring mismatches on undeclared items %s", car.vin,
                     [d.item for d in dropped])
        return seen.model_copy(update={"mismatches": kept})

    async def inspect(self, vin: str, views: list[str] | None = None, check_variant: bool = True,
                      images: list[Media] | None = None) -> InspectionResult:
        """Inspect a car at the gate. Re-inspection passes only the views that had findings.

        `images` lets a caller supply frames it captured itself. Capture and judgement are
        separable, and on CARLA they have to be: the gate camera path in this backend crashes
        the server, while the capture in scripts/demo_carla_day2.py does not. A caller that
        already holds frames should pass them rather than have this take them again.
        """
        car = self.rt.store.get(Car, vin)
        supplied = images is not None
        if images is None:
            # Deliberately inline. Capturing off-thread, concurrently with a running driving
            # loop, segfaults the CARLA server; capturing inline blocks the loop so no ticks
            # happen and no frame arrives. On CARLA, pass `images` instead.
            images = self.rt.gate_camera.capture(vin, views)
        view_names = [m.label.split()[0] for m in images]
        jobs = [self._view(img, GATE_VIEWS[name]) for img, name in zip(images, view_names)]
        if check_variant:
            # The build sheet is about the whole car, so the variant check wants every view even
            # when a re-inspection only re-shoots the zones that had findings. A caller that
            # supplied frames gets them reused: going back to the camera is what it was avoiding.
            variant_images = images if supplied else self.rt.gate_camera.capture(vin)
            jobs.append(self._variant(car, variant_images))
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
                                            confidence=f.confidence, image=img.uri,
                                            observation=f.observation or None))
        mismatches = [m for m in (variant.mismatches if variant else []) if m.confidence >= REVIEW_CONFIDENCE]
        clear_confidences = [v.clear_confidence for v in view_results]
        verdict = visual_verdict(kept, mismatches, clear_confidences,
                                 variant.confidence if variant else 1.0)
        # A REVIEW with no findings is otherwise unexplainable: it comes from an all-clear the
        # model was not confident enough about, not from anything it saw.
        log.info("visual: %s -> %s (clear confidences %s, variant %s)", vin, verdict.value,
                 [round(c, 2) for c in clear_confidences],
                 round(variant.confidence, 2) if variant else None)
        # The words behind a verdict are kept whichever way it went. A PASS with nothing to read is
        # indistinguishable from a stub that always passes, which is exactly the doubt a second
        # source is supposed to remove.
        observations = [f"{name}: {va.observation}" for name, va in zip(view_names, view_results)
                        if va.observation]
        result = InspectionResult(vin=vin, kind="visual", verdict=verdict, findings=findings,
                                  build_sheet_mismatches=[mismatch_text(m) for m in mismatches],
                                  observations=observations,
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
