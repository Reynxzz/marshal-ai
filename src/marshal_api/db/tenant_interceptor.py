"""Level 1 multi-tenancy: an ORM interceptor active on BOTH SQLite and PostgreSQL.

The current tenant lives in a :class:`ContextVar` so it is bound to the request
task rather than a connection.  Every SELECT against a tenant-scoped model has
``WHERE tenant_id = ...`` appended automatically; PostgreSQL RLS (Level 2) is
added on top of this by the migrations.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from sqlalchemy import event, text
from sqlalchemy.orm import Session

current_tenant_var: ContextVar[str] = ContextVar("current_tenant", default="")

# Execution option that opts a statement out of the automatic tenant filter,
# for genuinely cross-tenant administration queries only.
INCLUDE_ALL_TENANTS = "include_all_tenants"


def use_tenant(tenant_id: str) -> "_TenantScope":
    """Bind ``tenant_id`` to the current context for the duration of a block."""
    return _TenantScope(tenant_id)


def current_tenant() -> str:
    """The tenant bound to the current context, or ``""`` when unbound."""
    return current_tenant_var.get()


class _TenantScope:
    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._token: Token | None = None

    def __enter__(self) -> "_TenantScope":
        self._token = current_tenant_var.set(self._tenant_id)
        return self

    def __exit__(self, *exc_info) -> None:
        if self._token is not None:
            current_tenant_var.reset(self._token)
            self._token = None


def _is_tenant_scoped(statement) -> bool:
    """True when the statement's primary entity carries a ``tenant_id`` column."""
    for description in getattr(statement, "column_descriptions", ()):
        mapper = getattr(description.get("entity"), "__mapper__", None)
        if mapper is not None and "tenant_id" in mapper.columns:
            return True
    return False


@event.listens_for(Session, "do_orm_execute")
def enforce_tenant_isolation(orm_execute_state) -> None:
    """Append a ``tenant_id`` filter to every tenant-scoped SELECT."""
    tenant_id = current_tenant_var.get()
    if not tenant_id or not orm_execute_state.is_select:
        return
    if orm_execute_state.is_column_load or orm_execute_state.is_relationship_load:
        return
    if orm_execute_state.execution_options.get(INCLUDE_ALL_TENANTS):
        return
    if not _is_tenant_scoped(orm_execute_state.statement):
        return
    orm_execute_state.statement = orm_execute_state.statement.filter_by(
        tenant_id=tenant_id
    )


@event.listens_for(Session, "after_begin")
def publish_tenant_to_postgres(session, transaction, connection) -> None:
    """Set the ``app.current_tenant_id`` GUC the RLS policies read.

    PostgreSQL-only; transaction-local (``set_config(..., true)``) so the value
    cannot leak to the next transaction on a pooled connection.
    """
    if connection.dialect.name != "postgresql":
        return
    connection.execute(
        text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
        {"tenant_id": current_tenant_var.get()},
    )
