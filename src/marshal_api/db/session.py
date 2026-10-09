"""Dual-engine async session: SQLite for local dev, PostgreSQL for production.

The database URL is read once from ``MARSHAL_DB_URL`` and the same models, with
the same ULID keys and JSON columns, drive both engines.  SQLite gets the two
pragmas it needs (foreign keys, WAL) through a connection hook; PostgreSQL gets
JSONB through the :data:`DialectJSON` type.
"""

from __future__ import annotations

import os

from sqlalchemy import JSON, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Configurable database URL: defaults to local SQLite for rapid dev.
DATABASE_URL = os.getenv("MARSHAL_DB_URL", "sqlite+aiosqlite:///./marshal.db")

# Cross-dialect JSON column: JSON text in SQLite, binary JSONB in PostgreSQL.
DialectJSON = JSON().with_variant(postgresql.JSONB, "postgresql")

IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine_kwargs: dict = {"echo": False}
if IS_SQLITE:
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_async_engine(DATABASE_URL, **engine_kwargs)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    """Foreign keys are off by default in SQLite; WAL keeps readers from blocking."""
    if not IS_SQLITE:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.close()


async_session_maker = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncSession:
    async with async_session_maker() as session:
        yield session
