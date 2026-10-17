"""The demo plant's control channel, behind the dashboard's buttons (Flow 1).

Only the in-process plant (``MARSHAL_RUN_PLANT=1``, marshal_api.plant) answers these; on a host
that runs the real plant, writes are 409 — the CARLA run's records are read-only here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from marshal_api.core.deps import RequireRole, get_current_actor
from marshal_api.plant import Plant, PlantNotRunning, get_plant

router = APIRouter(tags=["plant"])

_supervisor = RequireRole(["supervisor"])


class AssistChoice(BaseModel):
    option: str


def _running(plant: Plant) -> Plant:
    if not plant.running:
        raise HTTPException(
            status_code=409,
            detail={"code": "plant_not_running",
                    "message": "This API is not running the demo plant (MARSHAL_RUN_PLANT=1)."},
        )
    return plant


@router.get("/plant/state")
async def plant_state(plant: Plant = Depends(get_plant)) -> dict:
    return plant.state()


@router.post("/plant/spawn", dependencies=[Depends(_supervisor)])
async def plant_spawn(plant: Plant = Depends(get_plant)) -> dict:
    return {"vin": _running(plant).spawn_now()}


@router.post("/plant/exception", dependencies=[Depends(_supervisor)])
async def plant_exception(plant: Plant = Depends(get_plant)) -> dict:
    return await _running(plant).exception()


@router.post("/plant/assist/{case_id}/decide", dependencies=[Depends(_supervisor)])
async def plant_decide(
    case_id: str,
    body: AssistChoice,
    plant: Plant = Depends(get_plant),
    actor=Depends(get_current_actor),
) -> dict:
    try:
        return await _running(plant).decide(case_id, body.option, actor.actor_id)
    except KeyError:
        raise HTTPException(status_code=404,
                            detail={"code": "case_not_found", "message": f"unknown case {case_id}"})
    except ValueError as exc:              # the car never offered this option
        raise HTTPException(status_code=422,
                            detail={"code": "option_not_offered", "message": str(exc)})


@router.post("/plant/restart", dependencies=[Depends(_supervisor)])
async def plant_restart(plant: Plant = Depends(get_plant)) -> dict:
    await _running(plant).restart()
    return {"restarted": True}
