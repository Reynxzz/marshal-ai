"""In-process async job queue (Phase 6).

The scaffolding exposes the seam the lab endpoints call: ``enqueue`` records a
job id and returns immediately. Actual background execution and progress
reporting are implemented in Phase 6.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Awaitable, Callable

from marshal_api.models.base import generate_ulid

JobHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class JobRunner:
    def __init__(self) -> None:
        self._tasks: dict[str, Any] = {}

    def new_job_id(self) -> str:
        return f"JOB-{generate_ulid()[:12]}"

    def enqueue(
        self, job_id: str, handler: JobHandler | None = None, payload: dict[str, Any] | None = None
    ) -> str:
        """Record a job for background execution and return its id."""
        self._tasks[job_id] = {"handler": handler, "payload": payload or {}}
        return job_id

    @property
    def active(self) -> int:
        return len(self._tasks)


@lru_cache
def get_job_runner() -> JobRunner:
    return JobRunner()
