"""Asynchronous job execution (Phase 6). Phase 2 ships the runner seam."""

from __future__ import annotations

from marshal_api.workers.job_runner import JobRunner, get_job_runner

__all__ = ["JobRunner", "get_job_runner"]
