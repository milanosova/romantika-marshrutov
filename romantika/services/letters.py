"""Letters to Mila (DOMAIN §2): what a participant sent that is not a report.

Four ways a letter appears — «Написать Миле» in the bot or in the Mini App, a message sent
outside a week, a report the author took back with «это не отчёт» — and one inbox for all of
them in the admin app. Mila's answer is written back on the row, whether she typed it in the
app or replied in her chat, so the inbox can say what is still unanswered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models


class Source(StrEnum):
    BOT = "bot"
    APP = "app"
    OUT_OF_WEEK = "out_of_week"
    NOT_REPORT = "not_report"


@dataclass(frozen=True, slots=True)
class LetterDTO:
    id: int
    season_id: int | None
    user_id: int
    report_id: int | None
    source: Source
    text: str
    created_at: datetime
    reply_text: str | None
    replied_at: datetime | None


def _dto(row: models.Letter) -> LetterDTO:
    return LetterDTO(
        id=row.id,
        season_id=row.season_id,
        user_id=row.user_id,
        report_id=row.report_id,
        source=Source(row.source),
        text=row.text,
        created_at=row.created_at,
        reply_text=row.reply_text,
        replied_at=row.replied_at,
    )


async def create(
    session: AsyncSession,
    *,
    season_id: int | None,
    user_id: int,
    source: Source,
    text: str | None,
    report_id: int | None = None,
    now: datetime,
) -> LetterDTO:
    row = models.Letter(
        season_id=season_id,
        user_id=user_id,
        report_id=report_id,
        source=source.value,
        text=(text or "").strip(),
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return _dto(row)


async def for_report(session: AsyncSession, report_id: int) -> LetterDTO | None:
    """The letter a report already became (sent outside a week, or taken back)."""
    query = select(models.Letter).where(models.Letter.report_id == report_id).order_by(models.Letter.id).limit(1)
    row = (await session.execute(query)).scalar_one_or_none()
    return None if row is None else _dto(row)


async def get(session: AsyncSession, letter_id: int) -> LetterDTO | None:
    row = await session.get(models.Letter, letter_id)
    return None if row is None else _dto(row)


async def list_for_season(session: AsyncSession, season_id: int, *, limit: int = 300) -> list[LetterDTO]:
    """Newest first; letters written before any season (season_id NULL) are shown with every season."""
    query = (
        select(models.Letter)
        .where((models.Letter.season_id == season_id) | (models.Letter.season_id.is_(None)))
        .order_by(models.Letter.created_at.desc(), models.Letter.id.desc())
        .limit(limit)
    )
    return [_dto(row) for row in (await session.execute(query)).scalars()]


async def unanswered_count(session: AsyncSession, season_id: int) -> int:
    query = select(func.count(models.Letter.id)).where(
        (models.Letter.season_id == season_id) | (models.Letter.season_id.is_(None)),
        models.Letter.replied_at.is_(None),
    )
    return int((await session.execute(query)).scalar_one())


async def mark_replied(
    session: AsyncSession, letter_id: int, *, reply_text: str, replied_by: int | None, now: datetime
) -> LetterDTO | None:
    """Record Mila's answer; a second answer to the same letter keeps the latest text."""
    row = await session.get(models.Letter, letter_id)
    if row is None:
        return None
    row.reply_text = reply_text
    row.replied_at = now
    row.replied_by = replied_by
    await session.flush()
    return _dto(row)
