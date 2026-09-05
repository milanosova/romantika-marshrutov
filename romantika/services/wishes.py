"""«От Милы» — one personal word per participant and season (DOMAIN §6)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models


async def set_wish(session: AsyncSession, *, season_id: int, user_id: int, text: str, now: datetime) -> None:
    """Write or rewrite the wish; there is exactly one per participant and season."""
    query = select(models.Wish).where(models.Wish.season_id == season_id, models.Wish.user_id == user_id)
    row = (await session.execute(query)).scalar_one_or_none()
    if row is None:
        row = models.Wish(season_id=season_id, user_id=user_id, created_at=now)
        session.add(row)
    row.text = text
    row.updated_at = now
    await session.flush()


async def get_wish(session: AsyncSession, season_id: int, user_id: int) -> str | None:
    query = select(models.Wish.text).where(models.Wish.season_id == season_id, models.Wish.user_id == user_id)
    return (await session.execute(query)).scalar_one_or_none()
