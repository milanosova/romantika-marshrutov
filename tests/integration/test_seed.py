"""Integration tests for the season seed and the schema guarantees around it."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.config import DATA_DIR
from romantika.db import models
from romantika.services import seed

SEASON_JSON = DATA_DIR / "seasons" / "mexico-2026.json"


async def test_import_reports_what_it_created(db_session: AsyncSession) -> None:
    first = await seed.import_season(db_session, SEASON_JSON)
    assert (first.slug, first.created) == ("mexico-2026", True)
    assert (first.weeks, first.weeks_created) == (12, 12)
    assert (first.achievement_types, first.achievement_types_created) == (9, 9)

    second = await seed.import_season(db_session, SEASON_JSON)
    assert second.created is False
    assert (second.weeks_created, second.achievement_types_created) == (0, 0)
    assert second.season_id == first.season_id


async def test_import_fills_the_season_fields(db_session: AsyncSession) -> None:
    await seed.import_season(db_session, SEASON_JSON)
    season = (await db_session.execute(select(models.Season).where(models.Season.slug == "mexico-2026"))).scalar_one()
    assert season.title_accusative == "Мексику"
    assert season.hashtag == "#мексика"
    assert season.daily_kind == "tzolkin"
    assert season.daily_title
    assert season.daily_note
    assert (season.base_freezes, season.max_freezes) == (2, 5)
    assert (season.level_tourist, season.level_traveler, season.level_resident) == (1, 4, 9)
    assert season.status == models.SeasonStatus.DRAFT.value

    week = (
        await db_session.execute(select(models.Week).where(models.Week.season_id == season.id, models.Week.number == 1))
    ).scalar_one()
    assert week.title == "За столом"
    assert week.word == "antojo"
    assert week.task_min and week.task_max and week.intro

    sorts = (
        (
            await db_session.execute(
                select(models.AchievementType.sort)
                .where(models.AchievementType.season_id == season.id)
                .order_by("sort")
            )
        )
        .scalars()
        .all()
    )
    assert sorts == list(range(9))


async def test_re_import_updates_edited_content_in_place(db_session: AsyncSession, tmp_path: Path) -> None:
    import json

    await seed.import_season(db_session, SEASON_JSON)
    payload = json.loads(SEASON_JSON.read_text(encoding="utf-8"))
    payload["weeks"][0]["title"] = "За столом (правка)"
    edited = tmp_path / "mexico-2026.json"
    edited.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    await seed.import_season(db_session, edited)

    weeks = (await db_session.execute(select(func.count()).select_from(models.Week))).scalar_one()
    assert weeks == 12
    title = (await db_session.execute(select(models.Week.title).where(models.Week.number == 1))).scalar_one()
    assert title == "За столом (правка)"


async def test_only_one_season_can_be_active(db_session: AsyncSession) -> None:
    result = await seed.import_season(db_session, SEASON_JSON)
    first = await db_session.get(models.Season, result.season_id)
    assert first is not None
    first.status = models.SeasonStatus.ACTIVE.value
    await db_session.flush()

    db_session.add(
        models.Season(
            slug="peru-2027",
            title="Перу",
            starts_on=first.ends_on,
            ends_on=first.ends_on,
            status=models.SeasonStatus.ACTIVE.value,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.pop("season"), "'season'"),
        (lambda payload: payload["weeks"][0].pop("title"), "'title'"),
        (lambda payload: payload["weeks"][0].pop("minimum"), "'minimum'"),
        (lambda payload: payload["achievements"][0].pop("name"), "'name'"),
    ],
)
async def test_import_refuses_a_file_missing_required_content(
    db_session: AsyncSession,
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    import json

    payload = json.loads(SEASON_JSON.read_text(encoding="utf-8"))
    mutate(payload)
    broken = tmp_path / "broken-2027.json"
    broken.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(seed.SeedError, match=message):
        await seed.import_season(db_session, broken)


async def test_re_import_can_move_the_whole_calendar(db_session: AsyncSession, tmp_path: Path) -> None:
    """Every week shifted by a day: the intermediate states overlap, the final one does not."""
    import json
    from datetime import date, timedelta

    await seed.import_season(db_session, SEASON_JSON)
    payload = json.loads(SEASON_JSON.read_text(encoding="utf-8"))
    for week in payload["weeks"]:
        for key in ("start", "end"):
            week[key] = (date.fromisoformat(week[key]) + timedelta(days=1)).isoformat()
    shifted = tmp_path / "mexico-2026.json"
    shifted.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = await seed.import_season(db_session, shifted)

    assert (result.weeks, result.weeks_created) == (12, 0)
    starts = (await db_session.execute(select(models.Week.starts_on).order_by(models.Week.number))).scalars().all()
    assert [str(day) for day in starts] == [week["start"] for week in payload["weeks"]]


async def test_re_import_of_an_overlapping_calendar_is_still_refused(db_session: AsyncSession, tmp_path: Path) -> None:
    import json

    await seed.import_season(db_session, SEASON_JSON)
    payload = json.loads(SEASON_JSON.read_text(encoding="utf-8"))
    payload["weeks"][1]["start"] = payload["weeks"][0]["start"]
    broken = tmp_path / "mexico-2026.json"
    broken.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(IntegrityError):
        await seed.import_season(db_session, broken)


async def test_re_import_reports_rows_the_file_no_longer_describes(db_session: AsyncSession, tmp_path: Path) -> None:
    import json

    await seed.import_season(db_session, SEASON_JSON)
    payload = json.loads(SEASON_JSON.read_text(encoding="utf-8"))
    payload["weeks"] = payload["weeks"][:11]
    payload["achievements"] = payload["achievements"][:8]
    shorter = tmp_path / "mexico-2026.json"
    shorter.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = await seed.import_season(db_session, shorter)

    # Seed never deletes; it says how many rows the season has and how many are orphaned.
    assert (result.weeks, result.weeks_stale) == (12, 1)
    assert (result.achievement_types, result.achievement_types_stale) == (9, 1)


async def test_re_import_refuses_a_key_described_twice(db_session: AsyncSession, tmp_path: Path) -> None:
    import json

    await seed.import_season(db_session, SEASON_JSON)
    payload = json.loads(SEASON_JSON.read_text(encoding="utf-8"))
    duplicate_week = dict(payload["weeks"][2], title="ДУБЛЬ", minimum="подменённый минимум")
    payload["weeks"].append(duplicate_week)
    payload["achievements"].append(dict(payload["achievements"][0], name="ДУБЛЬ-АЧИВКА"))
    doubled = tmp_path / "mexico-2026.json"
    doubled.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # On a re-import the database sees only an upsert onto an existing row, so nothing but
    # this check stands between a doubled line in the file and silently lost content.
    with pytest.raises(seed.SeedError, match="week 3 is described twice"):
        await seed.import_season(db_session, doubled)

    title = (await db_session.execute(select(models.Week.title).where(models.Week.number == 3))).scalar_one()
    assert title != "ДУБЛЬ"
