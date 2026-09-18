"""«Что мы узнали про страну» (DOMAIN §6).

Mila writes facts without an author, participants with one. Removing a fact marks it
deleted and writes an audit row; the text stays in the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.services import content, freezes, locks
from romantika.services.errors import Refused


@dataclass(frozen=True, slots=True)
class FactDTO:
    id: int
    text: str
    author_id: int | None
    week_id: int | None
    created_at: datetime


#: A fact fits in one Telegram message, like a report (the app's form has the same cap).
MAX_LENGTH = 4000


async def add(
    session: AsyncSession,
    *,
    season_id: int,
    week_id: int | None,
    text: str,
    author_id: int | None,
    now: datetime,
) -> int:
    """Add one fact; returns its id (the admin removes it by that id)."""
    body = text.strip()
    if not body:
        raise Refused("факт без текста не запишу")
    if len(body) > MAX_LENGTH:
        from romantika.texts import ru  # texts import FactDTO from here: a module-level import would loop

        raise Refused(ru.FACT_TOO_LONG)
    # Concurrent copies of one fact (a double tap, two devices) wait for each other here.
    await locks.serialise(session, f"fact:{season_id}:{author_id}")
    duplicate = await session.execute(
        select(models.Fact.id).where(
            models.Fact.season_id == season_id,
            models.Fact.author_id.is_(author_id) if author_id is None else models.Fact.author_id == author_id,
            models.Fact.deleted_at.is_(None),
            func.lower(models.Fact.text) == body.lower(),
        )
    )
    if duplicate.first() is not None:
        from romantika.texts import ru  # texts import FactDTO from here: a module-level import would loop

        raise Refused(ru.FACT_DUPLICATE)
    row = models.Fact(season_id=season_id, week_id=week_id, text=body, author_id=author_id, created_at=now)
    session.add(row)
    await session.flush()
    return row.id


@dataclass(frozen=True, slots=True)
class FactResult:
    fact_id: int
    freeze_granted: bool
    """The first own fact of the season earns a freeze, like the first own word (DOMAIN §3)."""


async def add_own(
    session: AsyncSession,
    *,
    season_id: int,
    week_id: int | None,
    text: str,
    author_id: int,
    now: datetime,
) -> FactResult:
    """A participant's own fact. Mila's facts go through `add`: hers are the club's, not personal."""
    first = await _count_of(session, season_id=season_id, author_id=author_id) == 0
    fact_id = await add(session, season_id=season_id, week_id=week_id, text=text, author_id=author_id, now=now)
    granted = first and await freezes.grant(
        session,
        season_id=season_id,
        user_id=author_id,
        reason=models.FreezeReason.FACT,
        granted_by=None,
        now=now,
    )
    return FactResult(fact_id=fact_id, freeze_granted=granted)


async def _count_of(session: AsyncSession, *, season_id: int, author_id: int) -> int:
    """How many facts this person has written this season, hidden ones included: the freeze
    is earned once, and a fact Mila hid does not give it back."""
    query = (
        select(func.count())
        .select_from(models.Fact)
        .where(models.Fact.season_id == season_id, models.Fact.author_id == author_id)
    )
    return int((await session.execute(query)).scalar_one())


async def list_active(session: AsyncSession, season_id: int, *, viewer_id: int | None = None) -> list[FactDTO]:
    """Facts of the season that were not removed, oldest first.

    With a `viewer_id`: Mila's facts (no author) plus the viewer's own — a participant's facts
    are personal (DOMAIN §6, 15.09.2026). Without one, everything: the admin's view.
    """
    query = (
        select(models.Fact)
        .where(models.Fact.season_id == season_id, models.Fact.deleted_at.is_(None))
        .order_by(models.Fact.created_at, models.Fact.id)
    )
    if viewer_id is not None:
        query = query.where(or_(models.Fact.author_id.is_(None), models.Fact.author_id == viewer_id))
    return [
        FactDTO(
            id=row.id,
            text=row.text,
            author_id=row.author_id,
            week_id=row.week_id,
            created_at=row.created_at,
        )
        for row in (await session.execute(query)).scalars()
    ]


async def remove(session: AsyncSession, *, fact_id: int, actor_id: int | None, now: datetime) -> bool:
    """Mark a fact removed; False when it does not exist or is already gone."""
    row = await session.get(models.Fact, fact_id)
    if row is None or row.deleted_at is not None:
        return False
    row.deleted_at = now
    content.audit(
        session,
        actor_id=actor_id,
        action="delete",
        entity="fact",
        entity_id=str(fact_id),
        before={"text": row.text},
        after=None,
    )
    await session.flush()
    return True
