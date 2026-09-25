"""Plant Ops Coordinator: the root agent the supervisor talks to."""

from __future__ import annotations

from google.adk.agents import Agent

from marshal_ai.agents.common import proposal_view, running_app
from marshal_ai.agents.inspection import functional_checker_agent, visual_inspector_agent
from marshal_ai.agents.remote_assistance import remote_assistance_agent
from marshal_ai.agents.report_router import report_router_agent
from marshal_ai.agents.test_lab import adversarial_tester_agent, scenario_generator_agent
from marshal_ai.agents.yard_traffic import yard_traffic_agent
from marshal_ai.config import settings
from marshal_ai.records import Car


async def plant_overview() -> dict:
    """Every car with state and location, open assistance cases, decisions waiting on humans, KPIs."""
    app = await running_app()
    return {
        "cars": [{"vin": c.vin, "state": c.state.value, "location": c.location, "slot": c.yard_slot,
                  "flags": c.flags} for c in app.rt.store.list(Car)],
        "open_assistance_cases": [c.case_id for c in app.assistance.open_cases()],
        "waiting_on_humans": [proposal_view(p) for p in app.rt.pending()],
        "kpis": app.kpis(),
    }


INSTRUCTION = """You are the Plant Ops Coordinator for Marshal. Marshal never drives a car: an existing,
validated driving system drives, and Marshal's agents run the operation around it and test it.

Use plant_overview for status questions. Route everything else to the right specialist:
- yard_traffic_controller: where cars go and when; late trucks, holds, closed zones, re-plan approvals.
- remote_assistance_desk: a car is stuck and has asked for help.
- visual_inspector: gate images, defects, build sheet mismatches, visual reviews.
- functional_checker: test track routine, lights, telemetry vs camera.
- report_and_rework_router: car reports, rework plans, bays, repair completion.
- scenario_generator: turn a situation or incident into a simulation test.
- adversarial_tester: find failing variations, regression suite, release gate.

Humans keep every decision that ships a car, sends it to rework, moves it near people, or releases new
driving software. When something waits on a human, say so plainly."""


root_agent = Agent(
    name="plant_ops_coordinator",
    model=settings.model,
    description="Root agent: routes supervisor requests to the control tower and test lab agents.",
    instruction=INSTRUCTION,
    tools=[plant_overview],
    sub_agents=[
        yard_traffic_agent,
        remote_assistance_agent,
        visual_inspector_agent,
        functional_checker_agent,
        report_router_agent,
        scenario_generator_agent,
        adversarial_tester_agent,
    ],
)
