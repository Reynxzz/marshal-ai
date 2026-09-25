"""Flow 3 and 4 agents: Visual Inspector and Functional Checker. Inspections run automatically."""

from __future__ import annotations

from google.adk.agents import Agent

from marshal_ai.agents.common import HUMAN_DECISION_RULE, proposal_view, running_app
from marshal_ai.config import settings
from marshal_ai.records import InspectionResult


async def get_visual_result(vin: str) -> dict:
    """The gate inspection for a car: verdict, findings per zone with image, build sheet mismatches."""
    app = await running_app()
    r = app.rt.store.get(InspectionResult, f"{vin}:visual")
    return r.model_dump(mode="json") if r else {"error": f"{vin} has not passed the gate yet"}


async def list_visual_reviews() -> list[dict]:
    """Visual findings with confidence 0.6 to 0.9, waiting for a human look."""
    app = await running_app()
    return [proposal_view(p) for p in app.rt.pending("review")]


async def decide_visual_review(proposal_id: str, confirm_defect: bool, supervisor: str) -> dict:
    """Record the supervisor's review: confirm the defect (car goes to rework) or clear it (car goes to the track)."""
    app = await running_app()
    r = await app.visual.decide_review(proposal_id, confirm_defect, supervisor)
    return {"vin": r.vin, "verdict": r.verdict.value}


async def get_checklist(vin: str) -> dict:
    """The test track checklist for a car: telemetry and camera per step, and where they disagree."""
    app = await running_app()
    r = app.rt.store.get(InspectionResult, f"{vin}:functional")
    if r is None:
        return {"error": f"{vin} has not run the test track yet"}
    return {**r.model_dump(mode="json"),
            "disagreements": [i.name for i in r.checklist
                              if i.telemetry_ok is not None and i.camera_ok is not None
                              and i.telemetry_ok != i.camera_ok]}


async def inspection_kpis() -> dict:
    """First-pass yield, review share, failures per checklist item, telemetry/camera disagreements."""
    app = await running_app()
    return {"visual": app.visual.kpis(), "functional": app.functional.kpis()}


visual_inspector_agent = Agent(
    name="visual_inspector",
    model=settings.model,
    description="Gate inspection results, defect findings, build sheet mismatches, visual review decisions.",
    instruction=f"""You are the Visual Inspector. Every car is photographed by four gate cameras; each body zone
is checked for defects and the car is compared with its build sheet. Confidence decides: over 0.9 all
clear passes, a defect over 0.9 fails, anything from 0.6 to 0.9 goes to a human review card.
Explain findings with zone, type, size, confidence and the image. {HUMAN_DECISION_RULE}""",
    tools=[get_visual_result, list_visual_reviews, decide_visual_review, inspection_kpis],
)

functional_checker_agent = Agent(
    name="functional_checker",
    model=settings.model,
    description="Test track checklist results: lights, indicators, reverse, brakes, steering.",
    instruction="""You are the Functional Checker. Each car runs routine R-12 on the test track: headlights,
indicators, brake lights, reverse, brake test, full-lock turns. Telemetry says what the car thinks it
did; the camera says what happened. An item passes only when both agree. When they disagree, say so
plainly, e.g. "telemetry reports the brake lights ON, but the camera shows the left one off".""",
    tools=[get_checklist, inspection_kpis],
)
