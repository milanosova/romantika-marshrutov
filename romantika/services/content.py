"""Seasons, weeks and settings: what participants read and what Mila edits (DOMAIN §1).

Every admin edit writes an `audit_log` row with the fields before and after it, because the
weekly texts are Mila's voice and a silent overwrite would be impossible to reconstruct.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
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
    announced_at: datetime | None = None

    @property
    def is_draft(self) -> bool:
        """Nobody but the admin sees a draft; it is never current and costs no freeze (DOMAIN §1)."""
        return self.announced_at is None

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
        announced_at=row.announced_at,
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


def _announced_filter() -> Any:
    """Weeks participants may see. A draft (`announced_at IS NULL`) must never become the
    current week — the bot would remind about «задание «»» and the passport would count it
    as a miss that costs everyone a freeze — so every participant-facing reader gets only
    announced weeks. The admin lists drafts too (`weeks(..., include_drafts=True)`)."""
    return models.Week.announced_at.is_not(None)


def ready_to_announce(week: WeekDTO | models.Week) -> bool:
    """What a draft needs before people may see it: a title and a minimum task."""
    return bool(week.title.strip()) and bool(week.task_min.strip())


async def weeks(session: AsyncSession, season_id: int, *, include_drafts: bool = False) -> list[WeekDTO]:
    """The season's weeks in calendar order (numbers and dates are independent, DOMAIN §1)."""
    query = select(models.Week).where(models.Week.season_id == season_id)
    if not include_drafts:
        query = query.where(_announced_filter())
    query = query.order_by(models.Week.starts_on, models.Week.number)
    return [_week_dto(row) for row in (await session.execute(query)).scalars()]


async def current_week(session: AsyncSession, season_id: int, *, today: date) -> WeekDTO | None:
    """The announced week that contains `today`; between weeks, outside the season and on a
    draft there is none."""
    query = select(models.Week).where(
        models.Week.season_id == season_id,
        models.Week.starts_on <= today,
        models.Week.ends_on >= today,
        _announced_filter(),
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
    """A week by its number, drafts included: this is how the admin addresses weeks."""
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
    if row.announced_at is not None:
        # An announced week never falls back to a draft: its stamps would be orphaned.
        for field_name in ("title", "task_min"):
            if field_name in changes and not str(changes[field_name]).strip():
                raise ValueError(f"week {row.number} is announced: '{field_name}' cannot be emptied")

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


# --- calendar: add, move, delete (DOMAIN §1, «недели буду делать сама») -----------


def _week_started(row: models.Week, today: date) -> bool:
    return row.starts_on <= today


async def _week_of_season(session: AsyncSession, week_id: int, season_id: int | None) -> models.Week:
    """The week row, and — when the caller names a season — only if it belongs to it.

    The admin session is scoped to the active season; a week of a draft next season must
    not be editable from it even though its id is guessable.
    """
    row = await session.get(models.Week, week_id)
    if row is None or (season_id is not None and row.season_id != season_id):
        raise ContentError(f"week {week_id} does not exist")
    return row


def _check_calendar(number: int, starts_on: date, ends_on: date, *, today: date, season: SeasonDTO) -> None:
    """The calendar rules a new or moved week must satisfy before the schema sees it.

    Inside the season (DOMAIN §1: the next country has its own dates), in the future, and
    well-formed. Overlap and a taken number are left to the schema and named afterwards.
    """
    if number < 1:
        raise ValueError(f"week number must be ≥ 1, got {number}")
    if ends_on < starts_on:
        raise ValueError(f"week {number} ends on {ends_on} before it starts on {starts_on}")
    if starts_on <= today:
        raise ContentError(f"week {number} would start on {starts_on}, not after today ({today})")
    if starts_on < season.starts_on or ends_on > season.ends_on:
        raise ContentError(
            f"week {number} ({starts_on}–{ends_on}) is outside the season ({season.starts_on}–{season.ends_on})"
        )


def _week_row_snapshot(row: models.Week) -> dict[str, Any]:
    return {
        "number": row.number,
        "title": row.title,
        "starts_on": row.starts_on.isoformat(),
        "ends_on": row.ends_on.isoformat(),
        "intro": row.intro,
        "task_min": row.task_min,
        "task_max": row.task_max,
        "word": row.word,
        "word_ru": row.word_ru,
        "word_meaning": row.word_meaning,
        "announced_at": row.announced_at.isoformat() if row.announced_at else None,
    }


async def announce_week(
    session: AsyncSession, *, actor_id: int | None, week_id: int, now: datetime, season_id: int | None = None
) -> WeekDTO:
    """Turn a draft into a week participants see. One way; needs a title and a minimum task.

    Announcing a week whose dates are already over is pointless and is refused, like editing
    it: nobody could have done it.
    """
    row = await _week_of_season(session, week_id, season_id)
    if row.announced_at is not None:
        return _week_dto(row)
    if not ready_to_announce(row):
        raise ValueError(f"week {row.number} cannot be announced without a title and a minimum task")
    if row.ends_on < now.date():
        raise ContentError(f"week {row.number} ended on {row.ends_on} and is not announced afterwards")
    row.announced_at = now
    audit(
        session,
        actor_id=actor_id,
        action="announce",
        entity="week",
        entity_id=str(week_id),
        before={"announced_at": None},
        after={"announced_at": now.isoformat()},
    )
    await session.flush()
    return _week_dto(row)


async def _week_has_participant_data(session: AsyncSession, week_id: int) -> bool:
    """Anything that points at this week: intents, reports, stamps, words, facts, reply links.

    Deleting a week with any of it would delete participant data (CLAUDE.md rule 1) — or,
    for `admin_links`, break the routing of Mila's replies. Every FK to `weeks` is listed.
    """
    clauses = [
        exists().where(models.WeekIntent.week_id == week_id),
        exists().where(models.Report.week_id == week_id),
        exists().where(models.Stamp.week_id == week_id),
        exists().where(models.Word.week_id == week_id),
        # A hidden fact is still a row that points at the week (nothing is ever deleted):
        # it keeps the week too, and the admin UI says so.
        exists().where(models.Fact.week_id == week_id),
        exists().where(models.AdminLink.week_id == week_id),
    ]
    return bool(await session.scalar(select(or_(*clauses))))


async def create_week(
    session: AsyncSession,
    *,
    actor_id: int | None,
    season_id: int,
    number: int,
    starts_on: date,
    ends_on: date,
    today: date,
    texts: Mapping[str, str] | None = None,
    announce: datetime | None = None,
) -> WeekDTO:
    """Add a week to a season. Only into the future: `starts_on` after today (Moscow).

    Created as a draft unless `announce` (the moment) is given and the texts allow it.

    The calendar rules live in the schema (number ≥ 1, ends ≥ starts, no overlap within
    a season, unique number); here they are turned into a `ContentError` the admin UI can
    show instead of a 500.
    """
    season = await require_season(session, season_id)
    _check_calendar(number, starts_on, ends_on, today=today, season=season)
    unknown = sorted(set(texts or {}) - EDITABLE_WEEK_FIELDS)
    if unknown:
        raise ValueError(f"week fields {unknown} are not editable (allowed: {sorted(EDITABLE_WEEK_FIELDS)})")

    fields: dict[str, str] = dict.fromkeys(EDITABLE_WEEK_FIELDS, "")
    fields.update(texts or {})
    row = models.Week(season_id=season_id, number=number, starts_on=starts_on, ends_on=ends_on, **fields)
    if announce:
        if not ready_to_announce(row):
            raise ValueError(f"week {number} cannot be announced without a title and a minimum task")
        row.announced_at = announce
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise ContentError(_calendar_conflict(exc, number, starts_on, ends_on)) from exc
    audit(
        session,
        actor_id=actor_id,
        action="create",
        entity="week",
        entity_id=str(row.id),
        before=None,
        after=_week_row_snapshot(row),
    )
    await session.flush()
    return _week_dto(row)


async def move_week(
    session: AsyncSession,
    *,
    actor_id: int | None,
    week_id: int,
    today: date,
    season_id: int | None = None,
    number: int | None = None,
    starts_on: date | None = None,
    ends_on: date | None = None,
) -> WeekDTO:
    """Change the number or the dates of a week that has not started yet.

    A started week keeps its calendar: the deadline was already named to people. The new
    dates must also stay in the future for the same reason.
    """
    row = await _week_of_season(session, week_id, season_id)
    if _week_started(row, today):
        raise ContentError(f"week {row.number} started on {row.starts_on} and its calendar is frozen")

    new_number = row.number if number is None else number
    new_starts = row.starts_on if starts_on is None else starts_on
    new_ends = row.ends_on if ends_on is None else ends_on
    _check_calendar(new_number, new_starts, new_ends, today=today, season=await require_season(session, row.season_id))

    full_before = {"number": row.number, "starts_on": row.starts_on.isoformat(), "ends_on": row.ends_on.isoformat()}
    full_after = {"number": new_number, "starts_on": new_starts.isoformat(), "ends_on": new_ends.isoformat()}
    before = {k: v for k, v in full_before.items() if full_after[k] != v}
    after = {k: v for k, v in full_after.items() if full_before[k] != v}
    if not after:
        return _week_dto(row)

    try:
        async with session.begin_nested():
            row.number, row.starts_on, row.ends_on = new_number, new_starts, new_ends
            await session.flush()
    except IntegrityError as exc:
        raise ContentError(_calendar_conflict(exc, new_number, new_starts, new_ends)) from exc
    audit(session, actor_id=actor_id, action="move", entity="week", entity_id=str(week_id), before=before, after=after)
    await session.flush()
    return _week_dto(row)


async def delete_week(
    session: AsyncSession, *, actor_id: int | None, week_id: int, today: date, season_id: int | None = None
) -> None:
    """Remove a week that has not started and that nobody has touched.

    Participant data is never deleted (CLAUDE.md rule 1), so a week with intents, reports,
    stamps, words or facts stays; the admin UI says why.
    """
    row = await _week_of_season(session, week_id, season_id)
    if _week_started(row, today):
        raise ContentError(f"week {row.number} started on {row.starts_on} and is not deleted")
    if await _week_has_participant_data(session, week_id):
        raise ContentError(f"week {row.number} has participant data and is not deleted")

    audit(
        session,
        actor_id=actor_id,
        action="delete",
        entity="week",
        entity_id=str(week_id),
        before=_week_row_snapshot(row),
        after=None,
    )
    try:
        async with session.begin_nested():
            await session.delete(row)
            await session.flush()
    except IntegrityError as exc:
        raise ContentError(f"week {row.number} is still referenced and is not deleted") from exc
    await session.flush()


def _calendar_conflict(exc: IntegrityError, number: int, starts_on: date, ends_on: date) -> str:
    """Name the schema rule that refused the calendar change, in words the admin UI can show."""
    message = str(exc.orig)
    if "weeks_no_overlap" in message:
        return f"week {number} ({starts_on}–{ends_on}) overlaps another week of the season"
    if "uq_weeks_season_id_number" in message:
        return f"week number {number} is already taken in this season"
    return f"week {number} ({starts_on}–{ends_on}) breaks a calendar rule: {message.splitlines()[0]}"


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
