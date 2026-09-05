"""Everything that is not a command, a button or an answer to the bot is a report."""

from __future__ import annotations

import logging
from datetime import date, datetime

from aiogram import Bot, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.bot import keyboards
from romantika.bot.send import safe_send
from romantika.domain.types import ReportKind
from romantika.services import content, jobs, letters, links, reports
from romantika.services.content import SeasonDTO
from romantika.services.gateways import TelegramGateway
from romantika.services.media import MediaStore
from romantika.services.people import UserDTO
from romantika.services.reports import AcceptResult, IncomingFile, IncomingMessage
from romantika.texts import ru

logger = logging.getLogger(__name__)


def incoming_from(message: Message) -> IncomingMessage | None:
    """Map a Telegram message to a report; None when it is not something we accept."""
    text = (message.text or message.caption or "").strip() or None
    chat_id, message_id = message.chat.id, message.message_id
    if message.photo:
        best = message.photo[-1]
        file = IncomingFile(
            kind=ReportKind.PHOTO,
            file_id=best.file_id,
            file_unique_id=best.file_unique_id,
            mime="image/jpeg",
            size=best.file_size,
            width=best.width,
            height=best.height,
        )
        return IncomingMessage(
            kind=ReportKind.PHOTO, text=text, tg_chat_id=chat_id, tg_message_id=message_id, files=[file]
        )
    if message.video:
        v = message.video
        file = IncomingFile(
            kind=ReportKind.VIDEO,
            file_id=v.file_id,
            file_unique_id=v.file_unique_id,
            mime=v.mime_type or "video/mp4",
            size=v.file_size,
            width=v.width,
            height=v.height,
        )
        return IncomingMessage(
            kind=ReportKind.VIDEO, text=text, tg_chat_id=chat_id, tg_message_id=message_id, files=[file]
        )
    if message.video_note:
        n = message.video_note
        file = IncomingFile(
            kind=ReportKind.VIDEO_NOTE,
            file_id=n.file_id,
            file_unique_id=n.file_unique_id,
            mime="video/mp4",
            size=n.file_size,
            width=n.length,
            height=n.length,
        )
        return IncomingMessage(
            kind=ReportKind.VIDEO_NOTE, text=text, tg_chat_id=chat_id, tg_message_id=message_id, files=[file]
        )
    if message.document:
        d = message.document
        file = IncomingFile(
            kind=ReportKind.DOCUMENT,
            file_id=d.file_id,
            file_unique_id=d.file_unique_id,
            mime=d.mime_type,
            size=d.file_size,
        )
        return IncomingMessage(
            kind=ReportKind.DOCUMENT, text=text, tg_chat_id=chat_id, tg_message_id=message_id, files=[file]
        )
    if message.voice:
        vo = message.voice
        file = IncomingFile(
            kind=ReportKind.VOICE,
            file_id=vo.file_id,
            file_unique_id=vo.file_unique_id,
            mime=vo.mime_type or "audio/ogg",
            size=vo.file_size,
        )
        return IncomingMessage(
            kind=ReportKind.VOICE, text=text, tg_chat_id=chat_id, tg_message_id=message_id, files=[file]
        )
    if message.audio:
        a = message.audio
        file = IncomingFile(
            kind=ReportKind.AUDIO,
            file_id=a.file_id,
            file_unique_id=a.file_unique_id,
            mime=a.mime_type or "audio/mpeg",
            size=a.file_size,
        )
        return IncomingMessage(
            kind=ReportKind.AUDIO, text=text, tg_chat_id=chat_id, tg_message_id=message_id, files=[file]
        )
    if text:
        return IncomingMessage(kind=ReportKind.TEXT, text=text, tg_chat_id=chat_id, tg_message_id=message_id, files=[])
    return None


async def download_media(
    session: AsyncSession, result: AcceptResult, media_store: MediaStore, telegram: TelegramGateway, now: datetime
) -> None:
    """Fetch files right away; when Telegram fails the worker retries and the stamp stays."""
    for media_id in result.media_ids:
        try:
            await media_store.download(session, media_id, telegram, now=now)
        except Exception as exc:
            logger.warning("media_download_deferred", extra={"media_id": str(media_id), "error": str(exc)})
            await jobs.enqueue(session, "media_download", {"media_id": str(media_id)}, now=now)


async def copy_to_admin(
    bot: Bot,
    session: AsyncSession,
    *,
    admin_chat: int | None,
    user: UserDTO,
    message: Message,
    header: str,
    report_id: int | None,
    week_id: int | None,
    now: datetime,
    copy_attachment: bool,
    letter_id: int | None = None,
) -> None:
    if admin_chat is None or user.id == admin_chat:
        return
    sent_ids: list[int] = []
    head = await safe_send(bot, admin_chat, header)
    if head is not None:
        sent_ids.append(head.message_id)
    if copy_attachment:
        try:
            copied = await bot.copy_message(admin_chat, from_chat_id=message.chat.id, message_id=message.message_id)
            sent_ids.append(copied.message_id)
        except Exception as exc:
            logger.warning("copy_to_admin_failed", extra={"user_id": user.id, "error": str(exc)})
    for message_id in sent_ids:
        await links.remember(
            session,
            admin_chat_id=admin_chat,
            admin_message_id=message_id,
            user_id=user.id,
            report_id=report_id,
            week_id=week_id,
            letter_id=letter_id,
            now=now,
        )


async def handle_report(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user: UserDTO,
    season: SeasonDTO | None,
    is_admin: bool,
    admin_chat: int | None,
    now: datetime,
    today: date,
    media_store: MediaStore,
    telegram: TelegramGateway,
) -> None:
    chat_id = message.chat.id
    if season is None:
        await safe_send(bot, chat_id, ru.NO_SEASON, reply_markup=keyboards.main_keyboard(is_admin=is_admin))
        return
    incoming = incoming_from(message)
    if incoming is None:
        await safe_send(bot, chat_id, ru.NOT_UNDERSTOOD, reply_markup=keyboards.main_keyboard(is_admin=is_admin))
        return

    result = await reports.accept(session, season_id=season.id, user_id=user.id, message=incoming, now=now)
    await download_media(session, result, media_store, telegram, now)
    author = user.display_name_with_username

    if result.out_of_week or result.week_number is None:
        await safe_send(bot, chat_id, ru.OUT_OF_WEEK, reply_markup=keyboards.main_keyboard(is_admin=is_admin))
        letter = await letters.create(
            session,
            season_id=season.id,
            user_id=user.id,
            source=letters.Source.OUT_OF_WEEK,
            text=incoming.text,
            report_id=result.report_id,
            now=now,
        )
        await copy_to_admin(
            bot, session, admin_chat=admin_chat, user=user, message=message,
            header=ru.admin_out_of_week_header(author, incoming.text, incoming.kind.value),
            report_id=result.report_id, week_id=None, now=now, copy_attachment=bool(incoming.files),
            letter_id=letter.id,
        )  # fmt: skip
        return

    week = await content.week_by_number(session, season.id, result.week_number)
    assert week is not None
    # The answer talks about the week's stamp, so it has to name the level the stamp actually
    # has: a text sent after a photo does not take the star away (DOMAIN §2), and saying
    # «✅ штамп за неделю» there would tell the participant they had just lost it.
    level = result.stamp_level or result.level
    await safe_send(
        bot,
        chat_id,
        ru.report_reply(week, result.level, stamp_level=result.stamp_level, freeze_granted=result.freeze_granted),
        reply_markup=keyboards.report_buttons(week.number, level, result.report_id),
    )
    await copy_to_admin(
        bot, session, admin_chat=admin_chat, user=user, message=message,
        header=ru.admin_report_header(week.number, author, incoming.text, incoming.kind.value),
        report_id=result.report_id, week_id=week.id, now=now, copy_attachment=bool(incoming.files),
    )  # fmt: skip


def build() -> Router:
    router = Router(name="reports")
    router.message.register(handle_report)
    return router
