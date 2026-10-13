"""Flow 6 and 7 agents: Scenario Generator and Adversarial Tester."""

from __future__ import annotations

import asyncio

from google.adk.agents import Agent

from marshal_ai.agents.common import HUMAN_DECISION_RULE
from marshal_ai.app import RELEASES, get_app
from marshal_ai.config import settings
from marshal_ai.records import Scenario, ScenarioStatus


def list_test_requests() -> list[dict]:
    """Situations waiting to become tests: resolved assistance cases and typed requests."""
    return get_app().test_requests


async def generate_scenario(situation: str, source: str = "supervisor request") -> dict:
    """Turn a plain-language situation into a validated, dry-run scenario spec (waits for engineer review)."""
    result = await asyncio.to_thread(get_app().generator.generate, situation, source)
    return {"scenario_id": result.scenario.id, "status": result.scenario.status.value,
            "attempts": result.attempts, "errors": result.errors, "spec": result.scenario.spec,
            "dry_run_metrics": result.dry_run_metrics}


def review_scenario(scenario_id: str, approve: bool, engineer: str, note: str = "") -> dict:
    """Record an engineer's review of a generated spec; approved specs join the test library."""
    s = get_app().generator.review(scenario_id, approve, engineer, note)
    return {"scenario_id": s.id, "status": s.status.value}


def list_scenarios() -> list[dict]:
    """Every scenario: id, status, source, tags."""
    return [{"id": s.id, "status": s.status.value, "source": s.source, "tags": s.tags,
             "notes": s.notes[:200]} for s in get_app().rt.store.list(Scenario)]


async def run_adversarial_search(scenario_id: str) -> dict:
    """Search variations of a passing scenario for the smallest failing change (60 runs max)."""
    app = get_app()
    scenario = app.rt.store.get(Scenario, scenario_id)
    if scenario is None or scenario.status not in (ScenarioStatus.DRY_RUN_OK, ScenarioStatus.IN_LIBRARY):
        return {"error": f"{scenario_id} is not a runnable scenario"}
    try:
        result = await asyncio.to_thread(app.adversarial.search, scenario_id)
    except ValueError as e:
        return {"error": str(e)}
    return {"runs": len(result.runs), "robust": result.robust, "report": result.report(),
            "search_reasoning": result.reasoning, "regression_test": result.regression_id,
            "clip": result.minimal.run.clip_uri if result.minimal else None}


async def evaluate_release(release: str) -> dict:
    """Run the whole regression suite against a driving system release; blocked if any test fails."""
    app = get_app()
    profile = RELEASES.get(release)
    if profile is None:
        return {"error": f"unknown release; known: {sorted(RELEASES)}"}
    runner = app.runner_for(release)
    report = await asyncio.to_thread(app.release_gate.evaluate, release, runner)
    return {"summary": report.summary(), "failed": report.failed, "blocked": report.blocked}


def approve_release(release: str, engineer: str) -> dict:
    """Record an engineer approving a release. Refused while any regression test fails."""
    gate = get_app().release_gate
    report = gate.report(release)
    if report is None:
        return {"error": f"{release} has not been evaluated"}
    try:
        gate.approve(report, engineer)
    except PermissionError as e:
        return {"approved": False, "error": str(e)}
    return {"approved": True, "summary": report.summary()}


scenario_generator_agent = Agent(
    name="scenario_generator",
    model=settings.model,
    description="Turns situations and assistance cases into validated simulation tests; engineer review.",
    instruction=f"""You are the Scenario Generator. Plain-language situations and resolved assistance cases
become short JSON scenario specs, validated against the plant map, compiled by a fixed template (the
model never writes simulator code) and dry-run. Use generate_scenario, then show the spec briefly and
its dry-run metrics. Specs join the test library only after an engineer approves. {HUMAN_DECISION_RULE}""",
    tools=[list_test_requests, generate_scenario, review_scenario, list_scenarios],
)

adversarial_tester_agent = Agent(
    name="adversarial_tester",
    model=settings.model,
    description="Finds variations that make the driving system fail; regression suite and release gate.",
    instruction=f"""You are the Adversarial Tester. From a passing scenario, you search variations (light,
weather, occluder, trigger distance, worker speed, ego speed, a braking car ahead). A rule judge decides
every verdict; failures are shrunk to the smallest change and added to the regression suite. A release
is approved only when the whole suite passes, and only by an engineer.
Report failures in plain language: what changed, the metrics against the limits, the likely cause, the fix.
{HUMAN_DECISION_RULE}""",
    tools=[list_scenarios, run_adversarial_search, evaluate_release, approve_release],
)
