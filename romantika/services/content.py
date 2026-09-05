"""Seasons, weeks and settings: what participants read and what Mila edits (DOMAIN §1).

Every admin edit writes an `audit_log` row with the fields before and after it, because the
weekly texts are Mila's voice and a silent overwrite would be impossible to reconstruct.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.domain.types import LevelConfig, WeekInfo

#: The only week fields the admin UI may change (ARCHITECTURE §6.1).
EDITABLE_WEEK_FIELDS: frozenset[str] = frozenset(
    {"title", "intro", "task_min", "task_max", "word", "word_ru", "word_meaning"}
)


@dataclass(frozen=True, slots=True)
class SeasonDTO:
    id: int
    slug: str
    title: str
    title_accusative: str
    hashtag: str
    starts_on: date
    ends_on: date
    status: models.SeasonStatus
    daily_kind: str | None
    daily_title: str
    daily_note: str
    base_freezes: int
    max_freezes: int
    levels: LevelConfig
    journal_promise_on: date | None


@dataclass(frozen=True, slots=True)
class WeekDTO:
    id: int
    season_id: int
    number: int
    title: str
    starts_on: date
    ends_on: date
    intro: str
    task_min: str
    task_max: str
    word: str
    word_ru: str
    word_meaning: str

    @property
    def info(self) -> WeekInfo:
        """The calendar facts the pure domain functions work with."""
        return WeekInfo(number=self.number, title=self.title, starts_on=self.starts_on, ends_on=self.ends_on)

    def contains(self, day: date) -> bool:
        return self.starts_on <= day <= self.ends_on


class ContentError(LookupError):
    """A season or a week the caller referred to does not exist."""


def _season_dto(row: models.Season) -> SeasonDTO:
    return SeasonDTO(
        id=row.id,
        slug=row.slug,
        title=row.title,
        title_accusative=row.title_accusative,
        hashtag=row.hashtag,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        status=models.SeasonStatus(row.status),
        daily_kind=row.daily_kind,
        daily_title=row.daily_title,
        daily_note=row.daily_note,
        base_freezes=row.base_freezes,
        max_freezes=row.max_freezes,
        levels=LevelConfig(tourist=row.level_tourist, traveler=row.level_traveler, resident=row.level_resident),
        journal_promise_on=row.journal_promise_on,
    )


def _week_dto(row: models.Week) -> WeekDTO:
    return WeekDTO(
        id=row.id,
        season_id=row.season_id,
        number=row.number,
        title=row.title,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        intro=row.intro,
        task_min=row.task_min,
        task_max=row.task_max,
        word=row.word,
        word_ru=row.word_ru,
        word_meaning=row.word_meaning,
    )


async def active_season(session: AsyncSession, *, today: date) -> SeasonDTO | None:
    """The one season that is running (DOMAIN §1); None before its first day.

    A season activated ahead of time stays invisible until `starts_on`; after the last day it
    is still the active season, so passports and the journal keep working until Mila archives
    it and activates the next country.
    """
    query = (
        select(models.Season)
        .where(models.Season.status == models.SeasonStatus.ACTIVE.value, models.Season.starts_on <= today)
        .order_by(models.Season.starts_on.desc())
        .limit(1)
    )
    row = (await session.execute(query)).scalar_one_or_none()
    return None if row is None else _season_dto(row)


async def get_season(session: AsyncSession, season_id: int) -> SeasonDTO | None:
    row = await session.get(models.Season, season_id)
    return None if row is None else _season_dto(row)


async def require_season(session: AsyncSession, season_id: int) -> SeasonDTO:
    season = await get_season(session, season_id)
    if season is None:
        raise ContentError(f"season {season_id} does not exist")
    return season


async def activate_season(session: AsyncSession, season_id: int, *, actor_id: int | None) -> SeasonDTO:
    """Make this season the active one; the previous active season is archived, not deleted."""
    row = await session.get(models.Season, season_id)
    if row is None:
        raise ContentError(f"season {season_id} does not exist")
    before = models.SeasonStatus(row.status)
    if before is models.SeasonStatus.ACTIVE:
        return _season_dto(row)

    others = (
        (
            await session.execute(
                select(models.Season).where(
                    models.Season.status == models.SeasonStatus.ACTIVE.value, models.Season.id != season_id
                )
            )
        )
        .scalars()
        .all()
    )
    for other in others:
        other.status = models.SeasonStatus.ARCHIVED.value
    # Two statements: the partial unique index allows one active season at a time.
    await session.flush()

    row.status = models.SeasonStatus.ACTIVE.value
    audit(
        session,
        actor_id=actor_id,
        action="activate",
        entity="season",
        entity_id=str(season_id),
        before={"status": before.value},
        after={"status": models.SeasonStatus.ACTIVE.value},
    )
    await session.flush()
    return _season_dto(row)


async def weeks(session: AsyncSession, season_id: int) -> list[WeekDTO]:
    query = select(models.Week).where(models.Week.season_id == season_id).order_by(models.Week.number)
    return [_week_dto(row) for row in (await session.execute(query)).scalars()]


async def current_week(session: AsyncSession, season_id: int, *, today: date) -> WeekDTO | None:
    """The week that contains `today`; between weeks and outside the season there is none."""
    query = select(models.Week).where(
        models.Week.season_id == season_id,
        models.Week.starts_on <= today,
        models.Week.ends_on >= today,
    )
    row = (await session.execute(query)).scalar_one_or_none()
    return None if row is None else _week_dto(row)


def daily_words(weeks: list[WeekDTO], current: WeekDTO | None, today: date) -> tuple[WeekDTO | None, WeekDTO | None]:
    """The word of the day and the «а помнишь?» word (DOMAIN §7).

    The word is the current week's, or the last released one between weeks; the memory word
    rotates daily over the other released weeks and appears only once there are two of them.
    """
    from romantika.domain.calendar import julian_day

    released = [week for week in weeks if week.word and week.starts_on <= today]
    word_week = current if current is not None and current.word else (released[-1] if released else None)
    older = [week for week in released if word_week is None or week.number != word_week.number]
    memory = older[julian_day(today) % len(older)] if len(older) >= 2 else None
    return word_week, memory


async def week_by_number(session: AsyncSession, season_id: int, number: int) -> WeekDTO | None:
    query = select(models.Week).where(models.Week.season_id == season_id, models.Week.number == number)
    row = (await session.execute(query)).scalar_one_or_none()
    return None if row is None else _week_dto(row)


async def update_week(
    session: AsyncSession,
    *,
    actor_id: int | None,
    week_id: int,
    changes: Mapping[str, str],
    today: date | None = None,
) -> WeekDTO:
    """Edit the texts of a week. Only content fields; the calendar is not editable here.

    «Прошедшие недели задним числом не меняем — люди их уже прожили» (DOMAIN §1, §8): with
    `today` given, a week that is already over is refused. Callers that edit content on
    behalf of an admin always pass the Moscow day; `None` skips the calendar check for
    fixtures and imports.
    """
    unknown = sorted(set(changes) - EDITABLE_WEEK_FIELDS)
    if unknown:
        raise ValueError(f"week fields {unknown} are not editable (allowed: {sorted(EDITABLE_WEEK_FIELDS)})")
    row = await session.get(models.Week, week_id)
    if row is None:
        raise ContentError(f"week {week_id} does not exist")
    if today is not None and row.ends_on < today:
        raise ContentError(f"week {row.number} ended on {row.ends_on} and is not edited afterwards")

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for field_name, value in changes.items():
        old = getattr(row, field_name)
        if old == value:
            continue
        before[field_name] = old
        after[field_name] = value
        setattr(row, field_name, value)
    if after:
        audit(
            session,
            actor_id=actor_id,
            action="update",
            entity="week",
            entity_id=str(week_id),
            before=before,
            after=after,
        )
    await session.flush()
    return _week_dto(row)


async def get_setting(session: AsyncSession, key: str, default: str | None = None) -> str | None:
    row = await session.get(models.Setting, key)
    return default if row is None else row.value


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(models.Setting, key)
    if row is None:
        row = models.Setting(key=key)
        session.add(row)
    row.value = value
    await session.flush()


def audit(
    session: AsyncSession,
    *,
    actor_id: int | None,
    action: str,
    entity: str,
    entity_id: str | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    """Append one audit row. Shared by every service that lets an admin change data."""
    session.add(
        models.AuditLog(
            actor_id=actor_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=before,
            after=after,
        )
    )
