"""Shared agent text and helpers."""

from __future__ import annotations

from marshal_ai.app import MarshalApp, get_app

HUMAN_DECISION_RULE = """Tools that take a person's name (supervisor, engineer, worker) record a human
decision. Call them only when that person has explicitly made the decision in this conversation, and
pass their name. Never decide on their behalf, and never infer approval."""


async def running_app() -> MarshalApp:
    app = get_app()
    await app.ensure_running()
    return app


def proposal_view(p) -> dict:
    return {"proposal_id": p.proposal_id, "kind": p.kind, "summary": p.summary, "status": p.status,
            "details": p.payload}
