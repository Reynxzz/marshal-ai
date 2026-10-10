"""Cross-cutting concerns: request context, errors, auth and dependencies."""

from __future__ import annotations

from marshal_api.core.context import (
    current_actor_id,
    current_tenant_id,
    use_actor,
    use_tenant,
)
from marshal_api.core.errors import ApiError

__all__ = [
    "ApiError",
    "current_actor_id",
    "current_tenant_id",
    "use_actor",
    "use_tenant",
]
