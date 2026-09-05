"""«Что мы узнали про страну» (DOMAIN §6).

Mila writes facts without an author, participants with one. Removing a fact marks it
deleted and writes an audit row; the text stays in the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.services import content
from romantika.services.errors import Refused


@dataclass(frozen=True, slots=True)
class FactDTO:
    id: int
    text: str
    author_id: int | None
    week_id: int | None
    created_at: datetime


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
    row = models.Fact(season_id=season_id, week_id=week_id, text=body, author_id=author_id, created_at=now)
    session.add(row)
    await session.flush()
    return row.id


async def list_active(session: AsyncSession, season_id: int) -> list[FactDTO]:
    """Facts of the season that were not removed, oldest first."""
    query = (
        select(models.Fact)
        .where(models.Fact.season_id == season_id, models.Fact.deleted_at.is_(None))
        .order_by(models.Fact.created_at, models.Fact.id)
    )
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
