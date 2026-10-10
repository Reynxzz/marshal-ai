"""Request-scoped multi-tenant context.

The tenant ContextVar is the same one the ORM interceptor reads, so setting it
here isolates database reads at Level 1 (and, on PostgreSQL, feeds the RLS GUC).
The actor identity is carried alongside it for auditing and RBAC.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from marshal_api.db.tenant_interceptor import (  # re-used: single tenant source
    current_tenant,
    use_tenant,
)

# Re-exported under the Control Tower's naming.
current_tenant_id = current_tenant

_actor_var: ContextVar[str] = ContextVar("current_actor", default="")


def current_actor_id() -> str:
    """The actor bound to the current context, or ``""`` when unbound."""
    return _actor_var.get()


class _ActorScope:
    def __init__(self, actor_id: str) -> None:
        self._actor_id = actor_id
        self._token: Token | None = None

    def __enter__(self) -> "_ActorScope":
        self._token = _actor_var.set(self._actor_id)
        return self

    def __exit__(self, *exc_info) -> None:
        if self._token is not None:
            _actor_var.reset(self._token)
            self._token = None


def use_actor(actor_id: str) -> "_ActorScope":
    """Bind ``actor_id`` to the current context for the duration of a block."""
    return _ActorScope(actor_id)


__all__ = [
    "current_actor_id",
    "current_tenant_id",
    "use_actor",
    "use_tenant",
]
