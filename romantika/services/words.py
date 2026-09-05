"""The season dictionary: words of the weeks plus the words participants send (DOMAIN §6).

Legacy put «слово — что значит» into one column; v2 splits it on the first « — », « - » or
« : » so the Mini App and the journal can show the word apart from its meaning. The first
word a participant adds earns a freeze.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.services import content, freezes, people
from romantika.services.errors import Refused

#: The first dash surrounded by spaces, or the first colon, separates word and meaning.
SEPARATOR = re.compile(r"\s+[—–-]\s+|\s*:\s*")
LEADING_SEPARATOR = re.compile(r"^[—–:-]\s*")

WORD_LENGTH = 255


@dataclass(frozen=True, slots=True)
class WordResult:
    word_id: int
    word: str
    meaning: str
    freeze_granted: bool


@dataclass(frozen=True, slots=True)
class WeekWord:
    """A word that came with a week of the season."""

    number: int
    title: str
    word: str
    word_ru: str
    meaning: str


@dataclass(frozen=True, slots=True)
class UserWord:
    """A word a participant added."""

    id: int
    user_id: int
    week_id: int | None
    word: str
    meaning: str
    added_at: datetime


@dataclass(frozen=True, slots=True)
class DictionaryView:
    week_words: list[WeekWord]
    user_words: list[UserWord]


def parse(raw: str) -> tuple[str, str]:
    """«sobremesa — время за столом» → ('sobremesa', 'время за столом'); no separator: no meaning."""
    text = raw.strip()
    if LEADING_SEPARATOR.match(text):
        # «— значение» / «: значение»: a meaning without a word
        return "", LEADING_SEPARATOR.sub("", text, count=1).strip()
    parts = SEPARATOR.split(text, maxsplit=1)
    word = parts[0].strip()
    meaning = parts[1].strip() if len(parts) > 1 else ""
    return word, meaning


async def add(
    session: AsyncSession,
    *,
    season_id: int,
    user_id: int,
    week_id: int | None,
    raw: str,
    now: datetime,
) -> WordResult:
    """Store one word of a participant; the first one of the season earns a freeze."""
    word, meaning = parse(raw)
    if not word:
        raise Refused("нужно само слово, а не только его значение")
    await people.ensure_member(session, season_id, user_id, now=now)
    if await _has_word(session, season_id=season_id, user_id=user_id, word=word[:WORD_LENGTH]):
        raise Refused(f"слово «{word[:WORD_LENGTH]}» у тебя в словарике уже есть")

    first = await _count(session, season_id=season_id, user_id=user_id) == 0
    row = models.Word(
        season_id=season_id,
        user_id=user_id,
        week_id=week_id,
        word=word[:WORD_LENGTH],
        meaning=meaning,
        created_at=now,
    )
    session.add(row)
    await session.flush()

    freeze_granted = False
    if first:
        freeze_granted = await freezes.grant(
            session,
            season_id=season_id,
            user_id=user_id,
            reason=models.FreezeReason.WORD,
            granted_by=None,
            now=now,
        )
    return WordResult(word_id=row.id, word=row.word, meaning=row.meaning, freeze_granted=freeze_granted)


async def season_dictionary(session: AsyncSession, season_id: int, *, today: date) -> DictionaryView:
    """Words of the weeks that have already started, plus every word participants added."""
    weeks = [
        WeekWord(number=week.number, title=week.title, word=week.word, word_ru=week.word_ru, meaning=week.word_meaning)
        for week in await content.weeks(session, season_id)
        if week.word and week.starts_on <= today
    ]
    query = (
        select(models.Word).where(models.Word.season_id == season_id).order_by(models.Word.created_at, models.Word.id)
    )
    user_words = [
        UserWord(
            id=row.id,
            user_id=row.user_id,
            week_id=row.week_id,
            word=row.word,
            meaning=row.meaning,
            added_at=row.created_at,
        )
        for row in (await session.execute(query)).scalars()
    ]
    return DictionaryView(week_words=weeks, user_words=user_words)


async def for_user(session: AsyncSession, *, season_id: int, user_id: int) -> list[UserWord]:
    """The words of one participant, for the journal."""
    query = (
        select(models.Word)
        .where(models.Word.season_id == season_id, models.Word.user_id == user_id)
        .order_by(models.Word.created_at, models.Word.id)
    )
    return [
        UserWord(
            id=row.id,
            user_id=row.user_id,
            week_id=row.week_id,
            word=row.word,
            meaning=row.meaning,
            added_at=row.created_at,
        )
        for row in (await session.execute(query)).scalars()
    ]


async def _has_word(session: AsyncSession, *, season_id: int, user_id: int, word: str) -> bool:
    query = (
        select(func.count())
        .select_from(models.Word)
        .where(
            models.Word.season_id == season_id,
            models.Word.user_id == user_id,
            func.lower(models.Word.word) == word.lower(),
        )
    )
    return int((await session.execute(query)).scalar_one()) > 0


async def _count(session: AsyncSession, *, season_id: int, user_id: int) -> int:
    query = (
        select(func.count())
        .select_from(models.Word)
        .where(models.Word.season_id == season_id, models.Word.user_id == user_id)
    )
    return int((await session.execute(query)).scalar_one())
