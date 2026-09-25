"""Flow 2 agent: Remote Assistance Desk. Cards are prepared automatically; a human chooses."""

from __future__ import annotations

from google.adk.agents import Agent

from marshal_ai.agents.common import HUMAN_DECISION_RULE, running_app
from marshal_ai.config import settings
from marshal_ai.driving import CommandRejected


async def list_assistance_cards() -> list[dict]:
    """Open assistance cases: situation, ranked options, recommendation, confidence, escalation."""
    app = await running_app()
    return [c.model_dump(mode="json") for c in app.assistance.open_cases()]


async def decide_assistance(case_id: str, option: str, supervisor: str) -> dict:
    """Relay the supervisor's chosen option to the car. Only offered options are accepted."""
    app = await running_app()
    try:
        case = await app.assistance.decide(case_id, option, supervisor)
    except (CommandRejected, KeyError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "case": case.model_dump(mode="json"), "sent_to_test_lab": True}


async def assistance_kpis() -> dict:
    """Cases per shift, resolution time, repeat locations."""
    return (await running_app()).assistance.kpis()


remote_assistance_agent = Agent(
    name="remote_assistance_desk",
    model=settings.model,
    description="Assistance cards for stuck cars; relays the supervisor's choice.",
    instruction=f"""You are the Remote Assistance Desk. When a car stops and asks for help, a card is prepared
automatically: one-line situation, the driving system's own options ranked, a recommendation.
Present cards clearly: situation, recommendation with confidence, the other options and their risk.
If people or moving equipment are in the scene, only a wait option is ever recommended.
The car stays stopped until a human answers; after 60 s the case escalates to the shift lead.
You never choose for the car. {HUMAN_DECISION_RULE}""",
    tools=[list_assistance_cards, decide_assistance, assistance_kpis],
)
