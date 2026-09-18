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


async def test_announced_at_downgrade_refuses_while_drafts_exist(engine: AsyncEngine, database_url: str) -> None:
    """`c4e8f1a2b9d3`: the previous release shows every week, so a draft must not survive a
    rollback unseen — the downgrade refuses until Mila announces or deletes it."""
    import pytest

    async with engine.begin() as connection:
        await connection.execute(sa.text("DELETE FROM weeks"))
        await connection.execute(sa.text("DELETE FROM seasons"))
        await connection.execute(
            sa.text(
                "INSERT INTO seasons (id, slug, title, title_accusative, hashtag, starts_on, ends_on, status) "
                "VALUES (9001, 'probe', 'Проба', 'Пробу', '#проба', '2026-01-05', '2026-03-29', 'draft')"
            )
        )
        await connection.execute(
            sa.text(
                "INSERT INTO weeks (season_id, number, title, starts_on, ends_on, announced_at) "
                "VALUES (9001, 1, '', '2026-01-05', '2026-01-11', NULL)"
            )
        )
    # Below `c4e8f1a2b9d3` by revision, not by `-1`: later migrations sit above it.
    with pytest.raises(Exception, match="draft week"):
        await asyncio.to_thread(run_alembic, database_url, "b7d4e2a90c15", downgrade=True)
    async with engine.connect() as connection:
        still_there = await connection.scalar(sa.text("SELECT count(*) FROM weeks WHERE announced_at IS NULL"))
    assert still_there == 1, "the refusal left the draft untouched"

    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE weeks SET announced_at = '2026-01-04 10:00+00', created_at = '2025-12-01 10:00+00' "
                "WHERE season_id = 9001"
            )
        )
    await asyncio.to_thread(run_alembic, database_url, "b7d4e2a90c15", downgrade=True)
    await asyncio.to_thread(run_alembic, database_url, "head")
    async with engine.connect() as connection:
        announced = await connection.scalar(sa.text("SELECT announced_at FROM weeks WHERE season_id = 9001"))
    # The exact moment is not kept across a round trip (the column is dropped); the state is.
    # The backfill restores «announced», dated by created_at — documented in the migration.
    assert announced is not None and announced.date().isoformat() == "2025-12-01", "announced, dated by created_at"
    async with engine.begin() as connection:
        await connection.execute(sa.text("DELETE FROM weeks WHERE season_id = 9001"))
        await connection.execute(sa.text("DELETE FROM seasons WHERE id = 9001"))


async def test_late_downgrade_refuses_while_late_reports_exist(engine: AsyncEngine, database_url: str) -> None:
    """`d5e6f7a8b9c0`: the previous release would count a late report for a stamp on the next
    recomputation, so the downgrade refuses until none is left."""
    import pytest

    async with engine.begin() as connection:
        await connection.execute(sa.text("DELETE FROM reports"))
        await connection.execute(sa.text("DELETE FROM season_members"))
        await connection.execute(sa.text("DELETE FROM weeks"))
        await connection.execute(sa.text("DELETE FROM seasons"))
        await connection.execute(sa.text("DELETE FROM users WHERE id = 9002"))
        await connection.execute(
            sa.text(
                "INSERT INTO seasons (id, slug, title, title_accusative, hashtag, starts_on, ends_on, status) "
                "VALUES (9001, 'probe', 'Проба', 'Пробу', '#проба', '2026-01-05', '2026-03-29', 'draft')"
            )
        )
        await connection.execute(sa.text("INSERT INTO users (id, first_name) VALUES (9002, 'Проба')"))
        await connection.execute(
            sa.text(
                "INSERT INTO reports (season_id, user_id, week_id, kind, level, late) "
                "VALUES (9001, 9002, NULL, 'text', 'min', true)"
            )
        )
    with pytest.raises(Exception, match="late report"):
        await asyncio.to_thread(run_alembic, database_url, "c4e8f1a2b9d3", downgrade=True)
    async with engine.begin() as connection:
        await connection.execute(sa.text("UPDATE reports SET late = false WHERE user_id = 9002"))
    await asyncio.to_thread(run_alembic, database_url, "c4e8f1a2b9d3", downgrade=True)
    await asyncio.to_thread(run_alembic, database_url, "head")
    async with engine.connect() as connection:
        late = await connection.scalar(sa.text("SELECT late FROM reports WHERE user_id = 9002"))
    assert late is False, "the round trip keeps every report on time"
    async with engine.begin() as connection:
        await connection.execute(sa.text("DELETE FROM reports WHERE user_id = 9002"))
        await connection.execute(sa.text("DELETE FROM users WHERE id = 9002"))
        await connection.execute(sa.text("DELETE FROM seasons WHERE id = 9001"))
