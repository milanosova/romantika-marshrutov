"""Job execution: one job per call, each step in its own transaction (ARCHITECTURE §9.1)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.db import models
from romantika.domain.calendar import to_moscow
from romantika.pdf.journal import journal_filename, render_journal_pdf
from romantika.services import content, jobs, journal, links, notify, reminders, stamps
from romantika.services.gateways import TelegramGateway
from romantika.services.media import MediaStore
from romantika.texts import ru

logger = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, dict[str, Any], "Context"], Awaitable[dict[str, Any] | None]]


class Context:
    def __init__(self, *, telegram: TelegramGateway, media_store: MediaStore, now: datetime) -> None:
        self.telegram = telegram
        self.media_store = media_store
        self.now = now


async def handle_media_download(session: AsyncSession, payload: dict[str, Any], ctx: Context) -> dict[str, Any] | None:
    media_id = uuid.UUID(str(payload["media_id"]))
    dto = await ctx.media_store.download(session, media_id, ctx.telegram, now=ctx.now)
    return {"path": dto.path}


async def handle_journal_pdf(session: AsyncSession, payload: dict[str, Any], ctx: Context) -> dict[str, Any] | None:
    user_id = int(payload["user_id"])
    season_id = int(payload["season_id"])
    chat_id = int(payload.get("chat_id", user_id))
    today = to_moscow(ctx.now).date()
    season = await content.require_season(session, season_id)
    view = await journal.build(session, season_id=season_id, user_id=user_id, today=today)
    pdf = render_journal_pdf(view, media_root=ctx.media_store.root)

    filename = journal_filename(season.title, view.user.first_name if view.user else None)
    relative = f"journals/{season.slug}/{user_id}/{ctx.now:%Y%m%d-%H%M%S}/{filename}"
    target = ctx.media_store.full_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(pdf)
    caption = (
        f"📔 Сезон «{season.title}» закончился. Вот твой журнал: недели, фото, слова, факты — всё, что ты прожил."
        if payload.get("requested_via") == "season_end"
        else f"📔 Твой журнал сезона «{season.title}»"
    )
    await ctx.telegram.send_document(chat_id, target, caption)
    return {"result_path": relative, "bytes": len(pdf)}


async def handle_broadcast(session: AsyncSession, payload: dict[str, Any], ctx: Context) -> dict[str, Any] | None:
    text = str(payload["text"])
    sent = 0
    for user_id in payload.get("user_ids", []):
        try:
            await ctx.telegram.send_message(int(user_id), text)
            sent += 1
        except Exception as exc:
            logger.warning("broadcast_not_delivered", extra={"user_id": user_id, "error": str(exc)})
    return {"sent": sent}


async def handle_telegram_notify(session: AsyncSession, payload: dict[str, Any], ctx: Context) -> dict[str, Any] | None:
    """Deliver what the web layer queued (`services.notify`): a text, then the files.

    A file uploaded through the Mini App gets its Telegram `file_id` here, on its first trip
    to Telegram; the bot can then re-send it by id (journal photos) without reading the disk.
    With `link` present every sent message is remembered for Mila's reply flow.
    """
    chat_id = int(payload["chat_id"])
    text = payload.get("text")
    link = payload.get("link")
    message_ids: list[int] = []
    skipped = 0
    if text:
        message_ids.append(await ctx.telegram.send_text(chat_id, str(text)))
    for raw in payload.get("media_ids", []):
        row = await session.get(models.Media, uuid.UUID(str(raw)))
        if row is None or row.downloaded_at is None or row.hidden_at is not None:
            skipped += 1
            logger.warning("notify_media_skipped", extra={"media_id": str(raw), "chat_id": chat_id})
            continue
        sent = await ctx.telegram.send_file(chat_id, ctx.media_store.full_path(row.path), mime=row.mime)
        message_ids.append(sent.message_id)
        if row.tg_file_id is None and sent.file_id:
            row.tg_file_id = sent.file_id
    if link:
        for message_id in message_ids:
            await links.remember(
                session,
                admin_chat_id=chat_id,
                admin_message_id=message_id,
                user_id=int(link["user_id"]),
                report_id=link.get("report_id"),
                week_id=link.get("week_id"),
                letter_id=link.get("letter_id"),
                now=ctx.now,
            )
    return {"sent": len(message_ids), "skipped": skipped}


async def handle_reminders_now(session: AsyncSession, payload: dict[str, Any], ctx: Context) -> dict[str, Any] | None:
    """«Напомнить сейчас» pressed in the admin Mini App: same texts and recipients as the bot."""
    season_id = int(payload["season_id"])
    week_number = payload.get("week_number")
    result = await reminders.send(
        session,
        season_id=season_id,
        week_number=int(week_number) if week_number is not None else None,
        telegram=ctx.telegram,
        now=ctx.now,
    )
    requested_by = payload.get("requested_by")
    if requested_by is not None:
        await ctx.telegram.send_message(int(requested_by), result.describe())
    return {"sent": result.sent, "total": result.total}


async def handle_season_journals(session: AsyncSession, payload: dict[str, Any], ctx: Context) -> dict[str, Any] | None:
    """The season is over: queue one `journal_pdf` per participant with a stamp (DOMAIN §7).

    One job per person, so a single broken journal never blocks the others; the admin gets
    a note with the count. Idempotent through the reminder log key the scheduler uses.
    """
    season_id = int(payload["season_id"])
    season = await content.require_season(session, season_id)
    recipients = sorted(await stamps.users_with_stamps(session, season_id))
    for user_id in recipients:
        await jobs.enqueue(
            session,
            "journal_pdf",
            {"user_id": user_id, "season_id": season_id, "chat_id": user_id, "requested_via": "season_end"},
            now=ctx.now,
        )
    admin_chat = payload.get("admin_chat")
    if admin_chat is not None:
        # Queued, not sent: a Telegram hiccup here must not roll the journal jobs back.
        await notify.enqueue_message(
            session,
            chat_id=int(admin_chat),
            text=(
                f"📔 Сезон «{season.title}» закончился — собираю журналы: {len(recipients)} "
                f"{ru.plural(len(recipients), 'человек', 'человека', 'человек')} со штампами."
            ),
            now=ctx.now,
        )
    return {"queued": len(recipients)}


HANDLERS: dict[str, Handler] = {
    "season_journals": handle_season_journals,
    "media_download": handle_media_download,
    "journal_pdf": handle_journal_pdf,
    "broadcast": handle_broadcast,
    notify.TELEGRAM_NOTIFY: handle_telegram_notify,
    notify.REMINDERS_NOW: handle_reminders_now,
}


async def run_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    telegram: TelegramGateway,
    media_store: MediaStore,
    now: datetime,
) -> str | None:
    """Claim one job, run it, record the outcome. Returns the job kind, or None when idle."""
    async with session_factory() as session, session.begin():
        job = await jobs.claim(session, now=now)
        if job is None:
            return None
        job_id, kind, payload = job.id, job.kind, dict(job.payload)

    handler = HANDLERS.get(kind)
    error: str | None = None
    result: dict[str, Any] | None = None
    if handler is None:
        error = f"unknown job kind {kind!r}"
    else:
        try:
            async with session_factory() as session, session.begin():
                result = await handler(session, payload, Context(telegram=telegram, media_store=media_store, now=now))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:2000]
            logger.warning("job_failed", extra={"job_id": job_id, "kind": kind, "error": error})

    async with session_factory() as session, session.begin():
        status = await jobs.finish(session, job_id, error=error, now=now, result=result)
    logger.info("job_finished", extra={"job_id": job_id, "kind": kind, "status": status.value})
    return kind
