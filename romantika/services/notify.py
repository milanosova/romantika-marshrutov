"""Telegram messages the web layer asks the worker to send (ARCHITECTURE §8.1, §9.1).

The web process never talks to Telegram itself: a report submitted in the Mini App, an intent,
a word, a letter — each becomes a `telegram_notify` job, and the worker delivers it with the
bot's gateway. That keeps the request fast and independent of Telegram being reachable, and
the delivery retried by the job queue like everything else.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from romantika.services import jobs

TELEGRAM_NOTIFY = "telegram_notify"
REMINDERS_NOW = "reminders_now"


async def enqueue_message(
    session: AsyncSession,
    *,
    chat_id: int,
    text: str | None,
    media_ids: Iterable[uuid.UUID] = (),
    link_user_id: int | None = None,
    link_report_id: int | None = None,
    link_week_id: int | None = None,
    link_letter_id: int | None = None,
    now: datetime,
) -> int:
    """Queue an HTML text and/or files for `chat_id`.

    With `link_user_id` set (Mila's chat), every message sent is remembered in `admin_links`,
    so Mila can answer it with a reply and the bot passes the answer to the participant —
    exactly what the bot does for reports it copies itself.
    """
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "media_ids": [str(media_id) for media_id in media_ids],
    }
    if link_user_id is not None:
        payload["link"] = {
            "user_id": link_user_id,
            "report_id": link_report_id,
            "week_id": link_week_id,
            "letter_id": link_letter_id,
        }
    return await jobs.enqueue(session, TELEGRAM_NOTIFY, payload, now=now)


async def enqueue_reminders_now(
    session: AsyncSession, *, season_id: int, requested_by: int, now: datetime, week_number: int | None = None
) -> int:
    """«Напомнить сейчас» from the admin Mini App; the worker reports back to `requested_by`.

    `week_number` is the week Mila is looking at; None means the week running right now.
    """
    payload = {"season_id": season_id, "requested_by": requested_by, "week_number": week_number}
    return await jobs.enqueue(session, REMINDERS_NOW, payload, now=now)
