"""Earned freezes (DOMAIN §3).

Only the earned ones are rows: the base freezes are a season constant and spending is
recomputed on every view by `rules.season_breakdown`, never stored.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.domain import rules
from romantika.services import content

#: Reasons the bot grants by itself, once per season and participant (DOMAIN §3).
AUTO_REASONS: frozenset[models.FreezeReason] = frozenset({models.FreezeReason.WORD, models.FreezeReason.MAX})

#: The partial unique index behind the «once per season» rule (migration 8f1c2a6d94b7).
AUTO_REASON_INDEX = "uq_freezes_auto_reason"

#: Advisory lock keys are `int4`; Telegram ids are wider, so they are folded into the range.
_LOCK_MODULUS = 2**31 - 1


async def grant(
    session: AsyncSession,
    *,
    season_id: int,
    user_id: int,
    reason: models.FreezeReason,
    granted_by: int | None,
    now: datetime,
    note: str | None = None,
) -> bool:
    """Give one freeze; False when the ceiling is reached or an automatic reason repeats.

    Both rules survive concurrent workers (an album arrives as one Telegram update per
    photo, and aiogram handles updates in parallel): the counting is serialized per season
    and participant by a transaction-scoped advisory lock, and the «once per season» rule is
    backed by the partial unique index `uq_freezes_auto_reason` on top of that.
    """
    await _lock(session, season_id=season_id, user_id=user_id)
    season = await content.require_season(session, season_id)
    bonus = await bonus_count(session, season_id, user_id)
    if season.base_freezes + bonus >= season.max_freezes:
        return False

    if reason not in AUTO_REASONS:
        session.add(
            models.Freeze(
                season_id=season_id,
                user_id=user_id,
                reason=reason.value,
                granted_by=granted_by,
                note=note,
                created_at=now,
            )
        )
        await session.flush()
        return True

    insert = (
        pg_insert(models.Freeze)
        .values(
            season_id=season_id,
            user_id=user_id,
            reason=reason.value,
            granted_by=granted_by,
            note=note,
            created_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=[models.Freeze.season_id, models.Freeze.user_id, models.Freeze.reason],
            index_where=text("reason IN ('word', 'max')"),
        )
        .returning(models.Freeze.id)
    )
    return (await session.execute(insert)).scalar_one_or_none() is not None


async def _lock(session: AsyncSession, *, season_id: int, user_id: int) -> None:
    """Hold the freeze counter of one participant until the transaction ends."""
    await session.execute(select(func.pg_advisory_xact_lock(season_id % _LOCK_MODULUS, user_id % _LOCK_MODULUS)))


async def bonus_count(session: AsyncSession, season_id: int, user_id: int) -> int:
    """How many freezes this participant earned on top of the season's base freezes."""
    query = (
        select(func.count())
        .select_from(models.Freeze)
        .where(models.Freeze.season_id == season_id, models.Freeze.user_id == user_id)
    )
    return int((await session.execute(query)).scalar_one())


async def bonus_counts(session: AsyncSession, season_id: int) -> dict[int, int]:
    """`{user_id: earned}` for the whole season, in one query."""
    query = (
        select(models.Freeze.user_id, func.count())
        .where(models.Freeze.season_id == season_id)
        .group_by(models.Freeze.user_id)
    )
    return {user_id: int(count) for user_id, count in (await session.execute(query)).all()}


async def total(session: AsyncSession, season_id: int, user_id: int) -> int:
    """Base plus earned, capped by the season ceiling (`rules.total_freezes`)."""
    season = await content.require_season(session, season_id)
    return rules.total_freezes(
        bonus_freezes=await bonus_count(session, season_id, user_id),
        base_freezes=season.base_freezes,
        max_freezes=season.max_freezes,
    )


async def reasons(session: AsyncSession, season_id: int, user_id: int) -> list[str]:
    """Reasons of the earned freezes in the order they were granted (passport footnote)."""
    query = (
        select(models.Freeze.reason)
        .where(models.Freeze.season_id == season_id, models.Freeze.user_id == user_id)
        .order_by(models.Freeze.created_at, models.Freeze.id)
    )
    return [str(reason) for (reason,) in (await session.execute(query)).all()]
