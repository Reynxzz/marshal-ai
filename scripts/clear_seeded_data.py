"""Script to remove all seeded transactional demo data from the Marshal database.

Leaves base infrastructure intact (Tenant, Plant, Zones, Rework Bays, Plant Map Points)
so the system is ready for clean production-like operations from scratch.
"""

from __future__ import annotations

import asyncio
from sqlalchemy import text
from marshal_api.db.session import async_session_maker

TABLES_TO_CLEAR = [
    "functional_step_checks",
    "visual_findings",
    "buildsheet_mismatches",
    "rework_tickets",
    "car_reports",
    "inspection_results",
    "assistance_options",
    "assistance_cases",
    "proposals",
    "missions",
    "build_sheets",
    "cars",
    "trucks",
    "release_eval_tests",
    "test_runs",
    "test_requests",
    "scenarios",
    "releases",
    "async_jobs",
    "event_logs",
    "media_artifacts",
]


async def clear_data() -> None:
    async with async_session_maker() as session:
        print("Clearing seeded transactional demo data...")
        for table in TABLES_TO_CLEAR:
            await session.execute(text(f"DELETE FROM {table}"))
            print(f"  - Cleared table: {table}")
        await session.commit()
        print("All seeded data successfully removed. Database is now in clean 'from scratch' state.")


if __name__ == "__main__":
    asyncio.run(clear_data())
