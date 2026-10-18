"""Aggregate every v1 router into one mountable router."""

from __future__ import annotations

from fastapi import APIRouter

from marshal_api.api.v1 import (
    assistance,
    batches,
    health,
    jobs,
    lab,
    media,
    overview,
    plant,
    proposals,
    quality,
    sim_bridge,
    yard,
)
from marshal_api.streaming import camera_proxy, sse

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(overview.router)
api_router.include_router(yard.router)
api_router.include_router(plant.router)
api_router.include_router(proposals.router)
api_router.include_router(assistance.router)
api_router.include_router(batches.router)
api_router.include_router(quality.router)
api_router.include_router(lab.router)
api_router.include_router(jobs.router)
api_router.include_router(media.router)
api_router.include_router(sse.router)
api_router.include_router(camera_proxy.router)
api_router.include_router(sim_bridge.router, prefix="/bridge", tags=["bridge"])
