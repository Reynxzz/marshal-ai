"""Flow 2: Remote assistance desk.

The driving system stops safely and asks. Gemini reads the clip and ranks the
driving system's own options; code enforces the rules; a human always chooses.
Every resolved case is sent to the test lab.
"""

from __future__ import annotations

import logging

from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from marshal_ai.driving import AssistChoose, AssistRequest
from marshal_ai.events import Topic
from marshal_ai.flows.base import ask, log
from marshal_ai.llm import Media
from marshal_ai.records import AssistanceCase, Car

if TYPE_CHECKING:
    from marshal_ai.app import MarshalApp

ESCALATE_AFTER = timedelta(seconds=60)


log = logging.getLogger(__name__)


class RankedOption(BaseModel):
    option: str
    reason: str
    risk: Literal["low", "medium", "high"]


class AssistAssessment(BaseModel):
    situation: str = Field(description="one line: what is in the way, where, how far the car is from it")
    people_or_moving_equipment: bool
    ranking: list[RankedOption] = Field(description="the offered options, best first")
    recommended: str | None
    confidence: float = Field(ge=0, le=1)


SYSTEM = """You are the Remote Assistance Desk for driverless cars in a car plant. The driving system
has stopped and offers options. Read the clip and rank only those options, best first, each with a
reason and a risk level. If a person or moving equipment is in the scene, only a wait option may be
recommended. Never recommend a high-risk option. Never invent an option."""


def is_wait(option: str) -> bool:
    return option.lower().startswith("wait")


def enforce_rules(a: AssistAssessment, options: list[str]) -> tuple[AssistAssessment, list[str]]:
    """Apply the desk's rules to Gemini's assessment. Returns the corrected assessment and notes."""
    notes = []
    ranking = [r for r in a.ranking if r.option in options]
    if len(ranking) < len(a.ranking):
        notes.append("dropped options the driving system did not offer")
    for opt in options:
        if opt not in {r.option for r in ranking}:
            if is_wait(opt):     # waiting keeps the car stopped: never the risky choice
                ranking.append(RankedOption(option=opt, reason="car stays stopped", risk="low"))
            else:
                ranking.append(RankedOption(option=opt, reason="not ranked by the model", risk="high"))
    risk = {r.option: r.risk for r in ranking}
    rec = a.recommended if a.recommended in options else None
    if a.recommended and rec is None:
        notes.append(f"recommendation {a.recommended!r} was not offered")
    if a.people_or_moving_equipment and rec is not None and not is_wait(rec):
        notes.append("people or moving equipment in scene: only wait may be recommended")
        rec = None
    if rec is not None and risk.get(rec) == "high":
        notes.append("high-risk option not recommended")
        rec = None
    if rec is None:
        candidates = [r.option for r in ranking
                      if r.risk != "high" and (is_wait(r.option) or not a.people_or_moving_equipment)]
        rec = candidates[0] if candidates else None
    confidence = a.confidence if rec == a.recommended else min(a.confidence, 0.5)
    return a.model_copy(update={"ranking": ranking, "recommended": rec, "confidence": confidence}), notes


class AssistanceDesk:
    def __init__(self, app: "MarshalApp"):
        self.app = app
        self.rt = app.rt

    def clip(self, request: AssistRequest) -> list[Media]:
        # A real backend can hand over the frame it captured when the car stopped. Without
        # bytes the media is only a gs:// URI, and Vertex AI cannot read a path that does not
        # exist - the 403 that made flow 3 unusable on the fake plant.
        supplier = getattr(self.rt.driving, "assist_media", None)
        if supplier is not None:
            media = supplier(request.case_id)
            if media is not None:
                return [media]
        truth = self.rt.world.scenes.get(request.case_id) if self.rt.world else None
        if truth is not None:                   # the fake plant describes the scene in words
            return [Media(mime="video/mp4", uri=request.clip_uri,
                          label="10 s clip before the stop", truth=truth)]
        # No bytes and nothing describing the scene. The URI on the request is the name the clip
        # would have had, not a file that exists, and sending it as readable media got a 403
        # from Vertex AI. The desk decides from the text of the request instead.
        if request.clip_uri is not None:
            log.info("assist %s: deciding without a clip; none was captured", request.case_id)
        return []

    async def on_request(self, request: AssistRequest) -> AssistanceCase:
        car = self.rt.store.get(Car, request.vin)
        prompt = (f"Car {request.vin} stopped at map point {request.location}.\n"
                  f"Driving system says: {request.reason}\n"
                  f"Offered options: {request.options}\n"
                  f"Car state: {car.state.value if car else 'unknown'}")
        assessment = await ask(self.rt, "assist.assess", prompt, AssistAssessment,
                               media=self.clip(request), system=SYSTEM)
        assessment, notes = enforce_rules(assessment, request.options)
        case = AssistanceCase(
            case_id=request.case_id, vin=request.vin, location=request.location or "",
            situation=assessment.situation, clip_uri=request.clip_uri, options=request.options,
            ranking=[r.model_dump() for r in assessment.ranking],
            people_or_moving_equipment=assessment.people_or_moving_equipment,
            recommended=assessment.recommended, confidence=assessment.confidence,
            opened_at=self.rt.clock(),
        )
        self.rt.store.put(case)
        if notes:
            log.info("assist %s: %s", request.case_id, "; ".join(notes))
        return case

    def open_cases(self) -> list[AssistanceCase]:
        return [c for c in self.rt.store.list(AssistanceCase) if c.resolved_by is None]

    async def decide(self, case_id: str, option: str, by: str) -> AssistanceCase:
        """A human picks an option. The mission client rejects options the car did not offer."""
        case = self.rt.store.get(AssistanceCase, case_id)
        if case is None:
            raise KeyError(f"unknown case {case_id}")
        await self.rt.missions.send(AssistChoose(case_id=case_id, vin=case.vin, option=option,
                                                 chosen_by=by))
        case = case.model_copy(update={"choice": option, "resolved_by": by,
                                       "resolved_at": self.rt.clock()})
        self.rt.store.put(case)
        await self.rt.bus.publish(Topic.TEST_REQUESTS, {
            "source": f"assistance case {case_id}",
            "text": (f"{case.situation} The driving system stopped and offered {case.options}; "
                     f"{by} chose {option}. Location: {case.location}."),
            "location": case.location, "case_id": case_id,
        })
        return case

    def check_escalations(self) -> list[str]:
        """Unanswered for 60 s: the car stays stopped and the case goes to the shift lead."""
        escalated = []
        for case in self.open_cases():
            if not case.escalated and self.rt.clock() - case.opened_at >= ESCALATE_AFTER:
                self.rt.store.put(case.model_copy(update={"escalated": True}))
                escalated.append(case.case_id)
                log.warning("assist %s escalated to shift lead", case.case_id)
        return escalated

    def kpis(self) -> dict:
        cases = self.rt.store.list(AssistanceCase)
        done = [(c.resolved_at - c.opened_at).total_seconds() for c in cases if c.resolved_at]
        per_location: dict[str, int] = {}
        for c in cases:
            per_location[c.location] = per_location.get(c.location, 0) + 1
        return {"cases": len(cases), "open": len(self.open_cases()),
                "avg_resolution_s": round(sum(done) / len(done), 1) if done else None,
                "repeat_locations": {k: v for k, v in per_location.items() if v > 1}}
