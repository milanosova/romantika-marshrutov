"""Stage 1 acceptance: schema, migrations, seed (ARCHITECTURE §4, §6 seed, §12).

READ-ONLY for implementers. Uses the `db_session` fixture from tests/conftest.py
(a session on a freshly migrated Postgres, rolled back after each test).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.services import seed

EXPECTED_TABLES = {
    "users",
    "seasons",
    "weeks",
    "achievement_types",
    "season_members",
    "intents",
    "reports",
    "media",
    "stamps",
    "freezes",
    "achievements",
    "words",
    "facts",
    "wishes",
    "admin_links",
    "dialog_states",
    "settings",
    "reminder_log",
    "audit_log",
    "jobs",
    "alembic_version",
}

SEASON_JSON = Path(__file__).resolve().parents[2] / "data" / "seasons" / "mexico-2026.json"
TZOLKIN_JSON = Path(__file__).resolve().parents[2] / "data" / "tzolkin.json"


async def test_all_tables_exist(db_session: AsyncSession) -> None:
    rows = await db_session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )
    names = {r[0] for r in rows}
    missing = EXPECTED_TABLES - names
    assert not missing, f"missing tables: {sorted(missing)}"


async def test_seed_mexico_is_idempotent(db_session: AsyncSession) -> None:
    assert SEASON_JSON.exists(), "data/seasons/mexico-2026.json must exist (copied from legacy)"
    await seed.import_season(db_session, SEASON_JSON)
    await seed.import_season(db_session, SEASON_JSON)  # second run changes nothing

    seasons = (await db_session.execute(text("SELECT slug, title, starts_on, ends_on FROM seasons"))).all()
    assert len(seasons) == 1
    slug, title, starts_on, ends_on = seasons[0]
    assert (slug, title) == ("mexico-2026", "Мексика")
    assert (str(starts_on), str(ends_on)) == ("2026-08-18", "2026-11-18")

    weeks = (
        await db_session.execute(text("SELECT number, title, starts_on, task_max FROM weeks ORDER BY number"))
    ).all()
    assert len(weeks) == 12
    assert weeks[0][0] == 1 and str(weeks[0][2]) == "2026-08-31"
    assert weeks[9][3] == "", "week 10 «Привал» has no maximum task"

    codes = [r[0] for r in (await db_session.execute(text("SELECT code FROM achievement_types ORDER BY sort"))).all()]
    assert len(codes) == 9
    assert {"повар", "художник", "полиглот", "кинозритель", "память", "проводник", "первый", "москва", "идея"} == set(
        codes
    )


async def test_stamp_uniqueness_and_levels(db_session: AsyncSession) -> None:
    await seed.import_season(db_session, SEASON_JSON)
    season_id = (await db_session.execute(text("SELECT id FROM seasons LIMIT 1"))).scalar_one()
    week_id = (await db_session.execute(text("SELECT id FROM weeks WHERE number = 1"))).scalar_one()
    db_session.add(models.User(id=1001, first_name="Test"))
    await db_session.flush()
    db_session.add(
        models.Stamp(season_id=season_id, user_id=1001, week_id=week_id, level="max", week_title_snapshot="w1", source="report")
    )
    await db_session.flush()
    db_session.add(
        models.Stamp(season_id=season_id, user_id=1001, week_id=week_id, level="min", week_title_snapshot="w1", source="report")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_invalid_enum_values_are_rejected(db_session: AsyncSession) -> None:
    await seed.import_season(db_session, SEASON_JSON)
    season_id = (await db_session.execute(text("SELECT id FROM seasons LIMIT 1"))).scalar_one()
    db_session.add(models.User(id=1002, first_name="Test"))
    await db_session.flush()
    db_session.add(models.Freeze(season_id=season_id, user_id=1002, reason="bogus"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


def test_tzolkin_data_file_is_single_source_of_truth() -> None:
    import json

    data = json.loads(TZOLKIN_JSON.read_text(encoding="utf-8"))
    assert len(data["signs"]) == 20
    assert len(data["tones"]) == 13
    assert data["correlation"] == 584283
    first = data["signs"][0]
    assert first["name"] == "Имиш"
    for key in ("name", "latin", "emoji", "symbol", "meaning", "destiny", "short", "day_advice"):
        assert key in first, f"sign is missing '{key}'"
