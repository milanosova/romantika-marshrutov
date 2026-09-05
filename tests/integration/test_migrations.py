"""The initial migration must be reversible (CLAUDE.md hard rule 2)."""

from __future__ import annotations

import asyncio

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import run_alembic


async def table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda sync_conn: sa.inspect(sync_conn).get_table_names())
    return set(names)


async def test_downgrade_base_then_upgrade_head(engine: AsyncEngine, database_url: str) -> None:
    # `run_alembic` starts its own event loop (async env.py), so it runs in a worker thread.
    await asyncio.to_thread(run_alembic, database_url, "base", downgrade=True)
    after_downgrade = await table_names(engine)
    assert after_downgrade <= {"alembic_version"}, f"downgrade left tables behind: {sorted(after_downgrade)}"

    await asyncio.to_thread(run_alembic, database_url, "head")
    after_upgrade = await table_names(engine)
    assert "seasons" in after_upgrade
    assert len(after_upgrade) == 22
