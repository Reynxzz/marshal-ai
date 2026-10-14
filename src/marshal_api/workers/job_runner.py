"""In-process async job queue backed by the ``async_jobs`` table.

``enqueue`` schedules a background asyncio task and returns immediately; the task
records its own progress/status in the database so the frontend can poll
``GET /jobs/{id}``. Handlers do their work in their own session, since the request
session is already closed by the time the task runs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from sqlmodel import select

from marshal_api.db.session import async_session_maker
from marshal_api.models.base import generate_ulid
from marshal_api.models.ops import AsyncJob

log = logging.getLogger("marshal.jobs")

JobHandler = Callable[[str, dict[str, Any], "JobRunner"], Awaitable[Optional[dict]]]


class JobRunner:
    def __init__(self, session_factory=async_session_maker) -> None:
        self._session_factory = session_factory
        self._tasks: dict[str, asyncio.Task] = {}

    def session(self):
        """A fresh session for a background handler (the request session is gone)."""
        return self._session_factory()

    def new_job_id(self) -> str:
        return f"JOB-{generate_ulid()[:12]}"

    def enqueue(
        self, job_id: str, handler: JobHandler, payload: dict[str, Any] | None = None
    ) -> str:
        task = asyncio.create_task(self._run(job_id, handler, payload or {}))
        self._tasks[job_id] = task
        return job_id

    @property
    def active(self) -> int:
        return sum(1 for task in self._tasks.values() if not task.done())

    async def set_progress(self, job_id: str, progress: int, message: str | None = None) -> None:
        await self._update(job_id, status="running", progress=progress, message=message)

    async def _run(self, job_id: str, handler: JobHandler, payload: dict[str, Any]) -> None:
        await self._update(job_id, status="running", progress=5)
        try:
            result = await handler(job_id, payload, self)
            await self._update(job_id, status="done", progress=100, result=result or {})
        except Exception as exc:  # a failed job is recorded, never raised into the request
            log.exception("Job %s failed", job_id)
            await self._update(job_id, status="failed", error=str(exc))

    async def _update(self, job_id: str, **fields: Any) -> None:
        async with self._session_factory() as session:
            job = (
                await session.execute(select(AsyncJob).where(AsyncJob.job_id == job_id))
            ).scalars().first()
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)
            session.add(job)
            await session.commit()


_JOB_RUNNER: JobRunner | None = None


def get_job_runner() -> JobRunner:
    global _JOB_RUNNER
    if _JOB_RUNNER is None:
        _JOB_RUNNER = JobRunner()
    return _JOB_RUNNER
