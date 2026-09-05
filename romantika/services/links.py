"""Routing of Mila's replies: which participant a message in the admin chat came from."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models


@dataclass(frozen=True, slots=True)
class LinkDTO:
    user_id: int
    report_id: int | None
    week_id: int | None
    letter_id: int | None = None


async def remember(
    session: AsyncSession,
    *,
    admin_chat_id: int,
    admin_message_id: int,
    user_id: int,
    report_id: int | None,
    week_id: int | None,
    now: datetime,
    letter_id: int | None = None,
) -> None:
    statement = pg_insert(models.AdminLink).values(
        admin_chat_id=admin_chat_id,
        admin_message_id=admin_message_id,
        user_id=user_id,
        report_id=report_id,
        week_id=week_id,
        letter_id=letter_id,
        created_at=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[models.AdminLink.admin_chat_id, models.AdminLink.admin_message_id],
        set_={"user_id": user_id, "report_id": report_id, "week_id": week_id, "letter_id": letter_id},
    )
    await session.execute(statement)


async def lookup(session: AsyncSession, *, admin_chat_id: int, admin_message_id: int) -> LinkDTO | None:
    row = await session.get(models.AdminLink, (admin_chat_id, admin_message_id))
    if row is None:
        return None
    return LinkDTO(user_id=row.user_id, report_id=row.report_id, week_id=row.week_id, letter_id=row.letter_id)
