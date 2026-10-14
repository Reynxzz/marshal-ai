"""Async job status endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from marshal_api.core.deps import get_current_tenant, get_session
from marshal_api.core.errors import ApiError
from marshal_api.models import AsyncJob
from marshal_api.schemas import Job
from marshal_api.schemas.common import JobStatus

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_current_tenant),
) -> Job:
    job = (
        await session.execute(
            select(AsyncJob).where(
                AsyncJob.tenant_id == tenant_id, AsyncJob.job_id == job_id
            )
        )
    ).scalars().first()
    if job is None:
        raise ApiError(404, "job_not_found", f"Job {job_id} not found")
    return Job(
        job_id=job.job_id,
        kind=job.kind,
        status=JobStatus(job.status),
        progress=job.progress,
        message=job.message,
        error=job.error,
        result=job.result or None,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
