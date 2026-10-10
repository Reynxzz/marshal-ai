"""Idempotency-key plumbing for retry-safe POST actions.

Phase 2 only extracts and records the key; the replay cache that returns the
first response for a repeated key is a later concern.
"""

from __future__ import annotations

import time

from fastapi import Header

from marshal_api.config import get_settings

_seen: dict[str, float] = {}


def get_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str | None:
    """Read ``Idempotency-Key`` and remember it for the configured TTL."""
    if not idempotency_key:
        return None
    ttl = get_settings().idempotency_ttl_seconds
    now = time.monotonic()
    for key, stamp in list(_seen.items()):
        if now - stamp > ttl:
            _seen.pop(key, None)
    _seen.setdefault(idempotency_key, now)
    return idempotency_key


def seen(key: str) -> bool:
    """Whether a key was recorded before this call."""
    return key in _seen
