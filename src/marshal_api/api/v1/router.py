"""Aggregate every v1 router into one mountable router."""

from __future__ import annotations

from fastapi import APIRouter

from marshal_api.api.v1 import (
    assistance,
    health,
    jobs,
    lab,
    media,
    overview,
    proposals,
    quality,
    yard,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(overview.router)
api_router.include_router(yard.router)
api_router.include_router(proposals.router)
api_router.include_router(assistance.router)
api_router.include_router(quality.router)
api_router.include_router(lab.router)
api_router.include_router(jobs.router)
api_router.include_router(media.router)
