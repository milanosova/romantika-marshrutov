"""Schema invariants that the database, not the application, has to guarantee."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.config import DATA_DIR
from romantika.db import models
from romantika.services import seed

SEASON_JSON = DATA_DIR / "seasons" / "mexico-2026.json"


async def _mexico_week_one(session: AsyncSession) -> models.Week:
    await seed.import_season(session, SEASON_JSON)
    return (await session.execute(select(models.Week).where(models.Week.number == 1))).scalar_one()


async def _other_season(session: AsyncSession, *, starts_on: date) -> models.Season:
    season = models.Season(
        slug="peru-2027",
        title="Перу",
        starts_on=starts_on,
        ends_on=starts_on + timedelta(days=90),
    )
    session.add(season)
    await session.flush()
    return season


async def test_weeks_of_one_season_may_not_overlap(db_session: AsyncSession) -> None:
    week = await _mexico_week_one(db_session)
    db_session.add(
        models.Week(
            season_id=week.season_id,
            number=99,
            title="overlap",
            starts_on=week.starts_on,
            ends_on=week.ends_on,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_weeks_of_different_seasons_may_share_dates(db_session: AsyncSession) -> None:
    week = await _mexico_week_one(db_session)
    other = await _other_season(db_session, starts_on=week.starts_on)
    db_session.add(
        models.Week(
            season_id=other.id,
            number=1,
            title="same dates, other season",
            starts_on=week.starts_on,
            ends_on=week.ends_on,
        )
    )
    await db_session.flush()


async def test_stamp_cannot_claim_a_season_its_week_does_not_belong_to(db_session: AsyncSession) -> None:
    week = await _mexico_week_one(db_session)
    other = await _other_season(db_session, starts_on=date(2027, 1, 4))
    db_session.add(models.User(id=2001, first_name="Test"))
    await db_session.flush()

    db_session.add(
        models.Stamp(
            season_id=other.id,
            user_id=2001,
            week_id=week.id,
            level="max",
            week_title_snapshot=week.title,
            source="report",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_report_cannot_claim_a_season_its_week_does_not_belong_to(db_session: AsyncSession) -> None:
    week = await _mexico_week_one(db_session)
    other = await _other_season(db_session, starts_on=date(2027, 1, 4))
    db_session.add(models.User(id=2002, first_name="Test"))
    await db_session.flush()

    db_session.add(models.Report(season_id=other.id, user_id=2002, week_id=week.id, kind="text", level="min"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_report_without_a_week_is_allowed(db_session: AsyncSession) -> None:
    week = await _mexico_week_one(db_session)
    db_session.add(models.User(id=2003, first_name="Test"))
    await db_session.flush()

    db_session.add(models.Report(season_id=week.season_id, user_id=2003, week_id=None, kind="other", level="min"))
    await db_session.flush()


@pytest.mark.parametrize("table", ["intents", "words", "facts"])
async def test_denormalized_season_must_match_the_week(db_session: AsyncSession, table: str) -> None:
    week = await _mexico_week_one(db_session)
    other = await _other_season(db_session, starts_on=date(2027, 1, 4))
    db_session.add(models.User(id=2100, first_name="Test"))
    await db_session.flush()

    rows = {
        "intents": models.WeekIntent(season_id=other.id, user_id=2100, week_id=week.id, choice="take"),
        "words": models.Word(season_id=other.id, user_id=2100, week_id=week.id, word="antojo"),
        "facts": models.Fact(season_id=other.id, week_id=week.id, text="cross-season"),
    }
    db_session.add(rows[table])
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_an_automatic_freeze_reason_is_unique_per_participant(db_session: AsyncSession) -> None:
    """`word` and `max` are granted once per season; the index says so, not only the service."""
    week = await _mexico_week_one(db_session)
    user = models.User(id=4242, first_name="Кон")
    db_session.add(user)
    await db_session.flush()

    def freeze(reason: models.FreezeReason) -> models.Freeze:
        return models.Freeze(season_id=week.season_id, user_id=user.id, reason=reason.value)

    db_session.add(freeze(models.FreezeReason.WORD))
    await db_session.flush()
    db_session.add(freeze(models.FreezeReason.WORD))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_a_manual_freeze_reason_repeats(db_session: AsyncSession) -> None:
    week = await _mexico_week_one(db_session)
    user = models.User(id=4343, first_name="Кон")
    db_session.add(user)
    await db_session.flush()
    for _ in range(2):
        db_session.add(models.Freeze(season_id=week.season_id, user_id=user.id, reason="manual"))
    await db_session.flush()
    rows = (await db_session.execute(select(models.Freeze).where(models.Freeze.user_id == user.id))).scalars().all()
    assert len(rows) == 2
