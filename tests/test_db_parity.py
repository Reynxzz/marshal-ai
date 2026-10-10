"""The data layer promised by docs-be/ERD_AND_DATA_MODELING.md.

Verifies the dual-engine contract without a PostgreSQL server: the schema is
compiled for both dialects, SQLite actually creates it, ULIDs are sortable, the
ORM tenant interceptor isolates reads, and the Alembic migration round-trips.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlmodel import SQLModel

import marshal_api.models  # noqa: F401  (registers tables on SQLModel.metadata)
from marshal_api.db.session import DialectJSON
from marshal_api.db.tenant_interceptor import (
    current_tenant,
    enforce_tenant_isolation,
    use_tenant,
)
from marshal_api.models import Car, CarState, Plant, Tenant
from marshal_api.models.base import generate_ulid

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    "tenants", "plants", "users", "zones", "rework_bays", "plant_map_points",
    "trucks", "cars", "build_sheets", "missions", "assistance_cases",
    "assistance_options", "inspection_results", "visual_findings",
    "buildsheet_mismatches", "functional_step_checks", "car_reports",
    "rework_tickets", "proposals", "scenarios", "test_requests", "test_runs",
    "releases", "release_eval_tests", "async_jobs", "media_artifacts",
    "event_logs",
}

EXPECTED_INDEXES = {
    "idx_cars_tenant_plant_state",
    "idx_cases_open_partial",
    "idx_cases_car_id",
    "idx_proposals_tenant_status",
    "idx_proposals_car_id",
    "idx_results_car_id",
    "idx_vf_result_id",
    "idx_fsc_result_id",
    "idx_bsm_result_id",
    "idx_options_case_id",
    "idx_scenarios_tenant_status",
    "idx_test_requests_tenant_status",
    "idx_runs_scenario_id",
    "idx_jobs_tenant_status",
    "idx_events_stream",
    "idx_rework_car_id",
    "idx_rework_report_id",
    "idx_ret_release_id",
}

EXPECTED_UNIQUES = {
    "uq_tenants_slug",
    "uq_tenant_plant_code",
    "uq_users_actor_id",
    "uq_users_email",
    "uq_cars_tenant_plant_vin",
    "uq_build_sheets_car_id",
    "uq_cases_case_id",
    "uq_proposals_proposal_id",
    "uq_results_result_id",
    "uq_reports_car_id",
    "uq_rework_ticket_id",
    "uq_scenarios_scenario_id",
    "uq_test_requests_request_id",
    "uq_test_runs_run_id",
    "uq_releases_release_id",
    "uq_jobs_job_id",
    "uq_media_media_id",
}

CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_all_erd_tables_registered():
    assert set(SQLModel.metadata.tables) == EXPECTED_TABLES


def test_index_catalog_matches_the_plan():
    indexes = {
        index.name
        for table in SQLModel.metadata.tables.values()
        for index in table.indexes
    }
    assert indexes == EXPECTED_INDEXES

    uniques = {
        constraint.name
        for table in SQLModel.metadata.tables.values()
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert EXPECTED_UNIQUES <= uniques


def test_no_standalone_tenant_or_plant_index():
    """The left-prefix rule: never index tenant_id/plant_id on their own."""
    for table in SQLModel.metadata.tables.values():
        for index in table.indexes:
            columns = [column.name for column in index.columns]
            assert columns not in (["tenant_id"], ["plant_id"])


def test_ulids_are_26_crockford_chars_and_sortable():
    first = generate_ulid()
    second = generate_ulid()

    assert len(first) == 26
    assert set(first) <= CROCKFORD
    assert first < second  # monotonic within the same process/millisecond


def test_dialect_json_is_jsonb_only_on_postgres():
    pg = str(CreateTable(Car.__table__).compile(dialect=postgresql.dialect()))
    lite = str(CreateTable(Car.__table__).compile(dialect=sqlite.dialect()))

    assert "JSONB" in pg
    assert "JSONB" not in lite
    assert isinstance(DialectJSON.dialect_impl(postgresql.dialect()), postgresql.JSONB)


def test_enums_are_portable_varchar_storing_values():
    state = Car.__table__.c.state.type

    assert state.native_enum is False
    assert "line_end" in state.enums
    assert "LINE_END" not in state.enums
    pg = str(CreateTable(Car.__table__).compile(dialect=postgresql.dialect()))
    assert "VARCHAR(30)" in pg
    assert "carstate" not in pg


def test_partial_index_has_where_on_both_engines():
    table = SQLModel.metadata.tables["assistance_cases"]
    index = next(i for i in table.indexes if i.name == "idx_cases_open_partial")

    for dialect in (sqlite.dialect(), postgresql.dialect()):
        ddl = str(CreateIndex(index).compile(dialect=dialect))
        assert "WHERE status != 'RESOLVED'" in ddl


async def test_tenant_interceptor_filters_reads(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'parity.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        session.add_all([
            Tenant(id="t1", name="Alpha", slug="alpha"),
            Tenant(id="t2", name="Beta", slug="beta"),
        ])
        session.add_all([
            Plant(id="p1", tenant_id="t1", code="P1", name="Plant 1"),
            Plant(id="p2", tenant_id="t2", code="P2", name="Plant 2"),
        ])
        session.add_all([
            Car(id="c1", tenant_id="t1", plant_id="p1", vin="VIN-1"),
            Car(id="c2", tenant_id="t2", plant_id="p2", vin="VIN-2"),
        ])
        await session.commit()

    assert current_tenant() == ""
    with use_tenant("t1"):
        assert current_tenant() == "t1"
        async with maker() as session:
            scoped = (await session.execute(select(Car))).scalars().all()
            assert [car.id for car in scoped] == ["c1"]

            # A model without tenant_id is left alone.
            tenants = (await session.execute(select(Tenant))).scalars().all()
            assert {t.id for t in tenants} == {"t1", "t2"}

            # Cross-tenant administration can opt out explicitly.
            everyone = (
                await session.execute(
                    select(Car).execution_options(include_all_tenants=True)
                )
            ).scalars().all()
            assert {car.id for car in everyone} == {"c1", "c2"}

    assert current_tenant() == ""
    await engine.dispose()


def test_interceptor_is_registered_on_session():
    assert event.contains(Session, "do_orm_execute", enforce_tenant_isolation)


def test_default_car_state_is_the_enum_value():
    car = Car(vin="VIN-X", tenant_id="t", plant_id="p")
    assert car.state is CarState.LINE_END


def test_alembic_migration_round_trips(tmp_path):
    db_path = tmp_path / "migrated.db"
    env = {**os.environ, "MARSHAL_DB_URL": f"sqlite+aiosqlite:///{db_path}"}

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert downgrade.returncode == 0, downgrade.stderr

    import sqlite3

    connection = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    connection.close()
    # Only Alembic's own bookkeeping table survives a full downgrade.
    assert tables <= {"alembic_version"}
