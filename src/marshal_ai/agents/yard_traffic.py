"""Flow 1 agent: Yard Traffic Controller."""

from __future__ import annotations

from datetime import timedelta

from google.adk.agents import Agent

from marshal_ai.agents.common import HUMAN_DECISION_RULE, proposal_view, running_app
from marshal_ai.config import settings
from marshal_ai.driving import CommandRejected, MissionGo, MissionHold, MissionResume
from marshal_ai.plant.state import Truck


async def get_yard_state() -> dict:
    """Cars, trucks by ETA, free yard slots, closed zones, rework bays, recent exception decisions."""
    app = await running_app()
    return {**app.yard.state_for_prompt(), "recent_decisions": app.yard.decisions[-5:],
            "kpis": app.yard.kpis()}


async def set_truck_eta(truck_id: str, minutes_from_now: float, scheduled_minutes_from_now: float | None = None) -> dict:
    """Record a truck's ETA (e.g. a delay reported by logistics). A delay of 15 min or more triggers re-slotting."""
    app = await running_app()
    now = app.rt.clock()
    truck = app.rt.plant.trucks.get(truck_id)
    eta = now + timedelta(minutes=minutes_from_now)
    if truck is None:
        scheduled = now + timedelta(minutes=scheduled_minutes_from_now if scheduled_minutes_from_now is not None
                                    else minutes_from_now)
        app.rt.plant.trucks[truck_id] = Truck(truck_id, scheduled, eta)
    else:
        truck.eta = eta
    await app.yard.tick()
    return {"truck_id": truck_id, "delay_min": round(app.rt.plant.trucks[truck_id].delay_min),
            "decisions": app.yard.decisions[-1:]}


async def set_zone_closed(zone: str, closed: bool) -> dict:
    """Close or reopen a plant zone, e.g. the test track for maintenance."""
    app = await running_app()
    if not app.rt.map.has_zone(zone):
        return {"error": f"unknown zone {zone}"}
    (app.rt.plant.closed_zones.add if closed else app.rt.plant.closed_zones.discard)(zone)
    await app.yard.tick()
    return {"closed_zones": sorted(app.rt.plant.closed_zones), "decisions": app.yard.decisions[-1:]}


async def send_mission(vin: str, waypoints: list[str], reason: str) -> dict:
    """Send one car through named map points. For several cars or a zone, the plan must be proposed instead."""
    app = await running_app()
    mission = MissionGo(mission_id=app.rt.next_id("M", 4), vin=vin, waypoints=waypoints)
    try:
        await app.rt.missions.send(mission)
    except (CommandRejected, KeyError) as e:
        return {"sent": False, "error": str(e)}
    return {"sent": True, "mission_id": mission.mission_id, "reason": reason}


async def hold_car(vin: str, reason: str, duration_s: float | None = None) -> dict:
    """Hold one car where it is, for duration_s seconds or until resumed."""
    app = await running_app()
    await app.rt.missions.send(MissionHold(vin=vin, duration_s=duration_s, reason=reason))
    return {"held": vin, "reason": reason}


async def resume_car(vin: str) -> dict:
    """Resume a held car's mission."""
    app = await running_app()
    await app.rt.missions.send(MissionResume(vin=vin))
    return {"resumed": vin}


async def list_replan_proposals() -> list[dict]:
    """Re-plans waiting for supervisor approval."""
    app = await running_app()
    return [proposal_view(p) for p in app.rt.pending("replan")]


async def decide_replan(proposal_id: str, approve: bool, supervisor: str) -> dict:
    """Record the supervisor's decision on a re-plan; approved missions are sent."""
    app = await running_app()
    return await app.yard.decide(proposal_id, approve, supervisor)


yard_traffic_agent = Agent(
    name="yard_traffic_controller",
    model=settings.model,
    description="Yard traffic: releases, slots, late trucks, closed zones, holds, re-plan approvals.",
    instruction=f"""You are the Yard Traffic Controller. Routine release and slotting run as rules every second.
Exceptions (late trucks, closed zones, full rework bays, the line outpacing the yard) are planned
automatically; plans that touch several cars or a zone wait for the supervisor.
- Use get_yard_state to answer questions; refer to places by named map points.
- Report truck ETAs and zone closures with set_truck_eta and set_zone_closed; then explain the resulting plan.
- Single-car moves: send_mission, hold_car, resume_car, always with a reason.
{HUMAN_DECISION_RULE}""",
    tools=[get_yard_state, set_truck_eta, set_zone_closed, send_mission, hold_car, resume_car,
           list_replan_proposals, decide_replan],
)
