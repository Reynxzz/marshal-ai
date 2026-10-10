"""Async job status endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from marshal_api.api.v1 import _stubs
from marshal_api.schemas import Job

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    return _stubs.job(job_id)
