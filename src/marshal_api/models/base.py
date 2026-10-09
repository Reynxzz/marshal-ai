"""ULID keys, UTC timestamps and the tenant-scoped mixin shared by the schemas.

Why ULID and not UUID: the first 10 Base32 characters encode a millisecond
timestamp, so new rows land at the right-hand edge of the B-Tree index on both
SQLite and PostgreSQL.  The remaining 80 bits are random, so the payload is not
guessable, and the string is a uniform ``VARCHAR(26)`` on both engines.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Type

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel
from ulid import ULID


def generate_ulid() -> str:
    """Generate a 26-character, lexicographically sortable ULID string."""
    return str(ULID())


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def enum_column(
    enum_cls: Type[Enum],
    *,
    length: int = 50,
    default: Enum | None = None,
    nullable: bool = False,
) -> Column:
    """A portable enum column: ``VARCHAR`` on both engines, storing ``.value``.

    ``native_enum=False`` keeps PostgreSQL from creating engine-specific enum
    types, so SQLite and PostgreSQL schemas stay identical and migrations need
    no ``CREATE TYPE``/``DROP TYPE`` handling.  ``values_callable`` stores the
    enum's value (``line_end``) rather than its member name (``LINE_END``).
    """
    return Column(
        sa.Enum(
            enum_cls,
            native_enum=False,
            length=length,
            values_callable=lambda cls: [member.value for member in cls],
            validate_strings=True,
            name=enum_cls.__name__.lower(),
        ),
        default=default,
        nullable=nullable,
    )


class TenantScopedModel(SQLModel):
    """Abstract base enforcing multi-tenancy, ULID-adjacent audit timestamps."""

    tenant_id: str = Field(
        foreign_key="tenants.id",
        max_length=26,
        nullable=False,
        description="Tenant / Organization ULID",
    )
    plant_id: str = Field(
        foreign_key="plants.id",
        max_length=26,
        nullable=False,
        description="Physical plant facility ULID",
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        sa_column_kwargs={"onupdate": utc_now},
    )
