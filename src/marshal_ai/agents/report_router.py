"""Flow 5 agent: Report and Rework Router."""

from __future__ import annotations

from google.adk.agents import Agent

from marshal_ai.agents.common import HUMAN_DECISION_RULE, proposal_view, running_app
from marshal_ai.config import settings
from marshal_ai.flows.report import BAYS
from marshal_ai.records import CarReport


async def get_car_report(vin: str) -> dict:
    """The one-page report for a car: verdict, summary, findings, failed checks, rework, re-inspection."""
    app = await running_app()
    r = app.rt.store.get(CarReport, vin)
    return r.model_dump(mode="json") if r else {"error": f"no report for {vin} yet"}


async def list_rework_proposals() -> list[dict]:
    """Rework plans waiting for supervisor confirmation."""
    app = await running_app()
    return [proposal_view(p) for p in app.rt.pending("rework")]


async def decide_rework(proposal_id: str, confirm: bool, supervisor: str) -> dict:
    """Record the supervisor's decision: confirm (car goes to the first bay) or clear the findings (car goes to the yard)."""
    app = await running_app()
    return await app.router.decide(proposal_id, confirm, supervisor)


async def mark_repair_done(vin: str, worker: str) -> dict:
    """Record that the bay finished the current repair; the car goes to the next bay or to re-inspection."""
    app = await running_app()
    if vin not in app.router.plans:
        return {"error": f"{vin} has no confirmed rework"}
    return await app.router.repair_done(vin, worker)


async def get_rework_bays() -> dict:
    """Rework bays, what they handle, typical time and who is in them."""
    app = await running_app()
    return {k: {**v, "occupants": app.rt.plant.bay_occupants.get(v["point"], [])} for k, v in BAYS.items()}


report_router_agent = Agent(
    name="report_and_rework_router",
    model=settings.model,
    description="Car reports, rework plans, bay status, repair completion.",
    instruction=f"""You are the Report and Rework Router. Every car gets one report merging the visual and
functional results. A car reaches shipping only with a PASS report. Failed cars get a rework plan, safety
items first; after repair only the failed items are re-inspected.
Answer with the report's verdict, the evidence, and where the car is in its rework. {HUMAN_DECISION_RULE}""",
    tools=[get_car_report, list_rework_proposals, decide_rework, mark_repair_done, get_rework_bays],
)
