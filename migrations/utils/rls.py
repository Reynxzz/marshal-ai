"""Level 2 multi-tenancy: PostgreSQL native Row-Level Security.

RLS is PostgreSQL-only.  On SQLite these helpers are deliberate no-ops, so a
migration that calls them stays portable across both engines.
"""

from __future__ import annotations

from alembic import op

POLICY_NAME = "tenant_isolation_policy"


def enable_rls(table_name: str) -> None:
    """Enable and force RLS with a tenant policy, on PostgreSQL only."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {POLICY_NAME} ON {table_name}
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')
        );
        """
    )


def disable_rls(table_name: str) -> None:
    """Drop the tenant policy and disable RLS, on PostgreSQL only."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table_name};")
    op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY;")
