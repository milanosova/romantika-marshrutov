"""The season journal of one participant (DOMAIN §7): bot preview, Mini App and PDF.

One view model for all three, so the PDF can never say something the bot does not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.domain.types import Level, StampLevel, WeekState
from romantika.services import achievements, content, facts, passport, people, stamps, wishes, words
from romantika.services.content import SeasonDTO
from romantika.services.facts import FactDTO
from romantika.services.people import UserDTO
from romantika.services.words import UserWord, WeekWord


@dataclass(frozen=True, slots=True)
class JournalMedia:
    """A file of a report. `path` only points at a real file once `downloaded` is true."""

    media_id: uuid.UUID
    path: str
    downloaded: bool
    tg_file_id: str | None
    """None until a Mini App upload has been sent to Telegram once; the bot then sends from disk."""
    week_id: int | None = None
    mime: str | None = None


@dataclass(frozen=True, slots=True)
class JournalWeek:
    """A week the participant has a stamp for, with what they wrote and shot that week.

    `quote` is the last line they wrote (the bot's short preview); `texts` are all of them in
    order and `media` the files, for the PDF where the week gets its own page section.
    """

    number: int
    title: str
    level: StampLevel
    quote: str
    starts_on: date | None = None
    ends_on: date | None = None
    texts: list[str] = field(default_factory=list)
    media: list[JournalMedia] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class JournalView:
    user: UserDTO | None
    season: SeasonDTO
    weeks: list[JournalWeek]
    media: list[JournalMedia]
    achievements: list[str]
    words: list[UserWord]
    facts: list[FactDTO]
    wish: str | None
    weeks_total: int = 0
    season_words: list[WeekWord] = field(default_factory=list)
    """Words of the weeks that have started, for the «Словарик сезона» block."""
    level: Level | None = None
    """The season status the passport shows (DOMAIN §4)."""
    frozen_weeks: set[int] = field(default_factory=set)
    """Weeks a freeze closed — shown as ❄️ in the PDF passport, not as a miss."""


async def build(session: AsyncSession, *, season_id: int, user_id: int, today: date) -> JournalView:
    """Collect the stamped weeks, the media, the achievements and the wish of one person.

    `today` keeps the journal honest about weeks that have not happened yet: a stamp Mila
    set on a future week is not shown before that week starts.
    """
    season = await content.require_season(session, season_id)
    weeks = {week.number: week for week in await content.weeks(session, season_id)}
    levels = await stamps.for_user(session, season_id=season_id, user_id=user_id)
    texts = await _texts(session, season_id=season_id, user_id=user_id)
    media = await _media(session, season_id=season_id, user_id=user_id)

    journal_weeks = [
        JournalWeek(
            number=number,
            title=weeks[number].title,
            level=level,
            quote=texts.get(weeks[number].id, [""])[-1],
            starts_on=weeks[number].starts_on,
            ends_on=weeks[number].ends_on,
            texts=texts.get(weeks[number].id, []),
            media=[item for item in media if item.week_id == weeks[number].id],
        )
        for number, level in sorted(levels.items())
        if number in weeks and weeks[number].starts_on <= today
    ]
    walk = await passport.build(session, season_id=season_id, user_id=user_id, today=today)
    return JournalView(
        user=await people.get_user(session, user_id),
        season=season,
        weeks=journal_weeks,
        level=walk.level,
        frozen_weeks={number for number, state in walk.breakdown.states.items() if state is WeekState.FROZEN},
        media=media,
        achievements=await achievements.labels(session, season_id=season_id, user_id=user_id),
        words=await words.for_user(session, season_id=season_id, user_id=user_id),
        facts=await facts.list_active(session, season_id),
        wish=await wishes.get_wish(session, season_id, user_id),
        weeks_total=len(weeks),
        season_words=(await words.season_dictionary(session, season_id, today=today)).week_words,
    )


async def wish_for(session: AsyncSession, *, season_id: int, user_id: int) -> str | None:
    """Mila's wish for the journal, if she has written one."""
    return await wishes.get_wish(session, season_id, user_id)


async def _texts(session: AsyncSession, *, season_id: int, user_id: int) -> dict[int, list[str]]:
    """`{week_id: [text, ...]}` — everything the participant wrote in that week, oldest first."""
    query = (
        select(models.Report.week_id, models.Report.text)
        .where(
            models.Report.season_id == season_id,
            models.Report.user_id == user_id,
            models.Report.week_id.is_not(None),
            models.Report.deleted_at.is_(None),
            models.Report.text.is_not(None),
            models.Report.text != "",
        )
        .order_by(models.Report.created_at, models.Report.id)
    )
    texts: dict[int, list[str]] = {}
    for week_id, text in (await session.execute(query)).tuples().all():
        if week_id is not None and text:
            texts.setdefault(week_id, []).append(text)
    return texts


async def _media(session: AsyncSession, *, season_id: int, user_id: int) -> list[JournalMedia]:
    """Files of week reports that were neither cancelled nor hidden.

    A message sent outside a week is a letter to Mila and not a report (DOMAIN §2), so its
    files stay out of the season journal. `downloaded` says whether the file is already on
    our disk: the row is created by `reports.accept` and filled in by `MediaStore.download`,
    so the PDF must skip a path that is still only a promise.
    """
    query = (
        select(
            models.Media.id,
            models.Media.path,
            models.Media.downloaded_at,
            models.Media.tg_file_id,
            models.Report.week_id,
            models.Media.mime,
        )
        .join(models.Report, models.Report.id == models.Media.report_id)
        .where(
            models.Report.season_id == season_id,
            models.Report.user_id == user_id,
            models.Report.week_id.is_not(None),
            models.Report.deleted_at.is_(None),
            models.Media.hidden_at.is_(None),
        )
        .order_by(models.Media.created_at, models.Media.id)
    )
    return [
        JournalMedia(
            media_id=media_id,
            path=path,
            downloaded=downloaded_at is not None,
            tg_file_id=tg_file_id,
            week_id=week_id,
            mime=mime,
        )
        for media_id, path, downloaded_at, tg_file_id, week_id, mime in (await session.execute(query)).all()
    ]


@dataclass(frozen=True, slots=True)
class ReportMediaDTO:
    media_id: uuid.UUID
    mime: str | None
    downloaded: bool


@dataclass(frozen=True, slots=True)
class ReportDTO:
    id: int
    week_number: int | None
    kind: str
    level: str
    text: str | None
    created_at: datetime
    media: list[ReportMediaDTO]
    edited_at: datetime | None = None


async def reports_for_user(session: AsyncSession, *, season_id: int, user_id: int) -> list[ReportDTO]:
    """Every non-cancelled report of the person in this season, newest first, with its files."""
    week_numbers = {week.id: week.number for week in await content.weeks(session, season_id)}
    query = (
        select(models.Report)
        .where(
            models.Report.season_id == season_id,
            models.Report.user_id == user_id,
            models.Report.deleted_at.is_(None),
        )
        .order_by(models.Report.created_at.desc(), models.Report.id.desc())
    )
    rows = list((await session.execute(query)).scalars())
    if not rows:
        return []
    media_query = (
        select(models.Media)
        .where(models.Media.report_id.in_([row.id for row in rows]), models.Media.hidden_at.is_(None))
        .order_by(models.Media.created_at, models.Media.id)
    )
    by_report: dict[int, list[ReportMediaDTO]] = {}
    for media in (await session.execute(media_query)).scalars():
        by_report.setdefault(media.report_id, []).append(
            ReportMediaDTO(media_id=media.id, mime=media.mime, downloaded=media.downloaded_at is not None)
        )
    return [
        ReportDTO(
            id=row.id,
            week_number=week_numbers.get(row.week_id) if row.week_id is not None else None,
            kind=row.kind,
            level=row.level,
            text=row.text,
            created_at=row.created_at,
            media=by_report.get(row.id, []),
            edited_at=row.edited_at,
        )
        for row in rows
    ]
