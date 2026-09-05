"""Screens shared by commands, buttons and callbacks."""

from __future__ import annotations

import logging
from datetime import date

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.bot import keyboards
from romantika.bot.send import safe_send
from romantika.config import Settings
from romantika.domain.tzolkin import tzolkin_day
from romantika.services import content, facts, freezes, journal, passport, people, words
from romantika.services.content import SeasonDTO
from romantika.services.media import MediaStore
from romantika.services.people import UserDTO
from romantika.texts import ru

logger = logging.getLogger(__name__)


async def send_task(bot: Bot, chat_id: int, session: AsyncSession, season: SeasonDTO | None, today: date) -> None:
    if season is None:
        await safe_send(bot, chat_id, ru.NO_SEASON)
        return
    week = await content.current_week(session, season.id, today=today)
    if week is None:
        await safe_send(bot, chat_id, ru.NO_WEEK_TASK)
        return
    await safe_send(bot, chat_id, ru.task_text(week), reply_markup=keyboards.task_buttons(week.number))


async def send_today(
    bot: Bot, chat_id: int, session: AsyncSession, season: SeasonDTO | None, today: date, settings: Settings
) -> None:
    if season is None:
        await safe_send(bot, chat_id, ru.NO_SEASON)
        return
    weeks = await content.weeks(session, season.id)
    current = await content.current_week(session, season.id, today=today)
    word_week, memory = content.daily_words(weeks, current, today)
    tz = tzolkin_day(today) if season.daily_kind == "tzolkin" else None
    text = ru.today_text(today, tzolkin=tz, word_week=word_week, memory_week=memory, note=season.daily_note)
    markup = keyboards.calendar_button(settings.public_base_url) if tz is not None else None
    await safe_send(bot, chat_id, text, reply_markup=markup)


async def send_passport(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    season: SeasonDTO | None,
    user_id: int,
    today: date,
    settings: Settings,
) -> None:
    if season is None:
        await safe_send(bot, chat_id, ru.NO_SEASON)
        return
    view = await passport.build(session, season_id=season.id, user_id=user_id, today=today)
    reasons = await freezes.reasons(session, season.id, user_id)
    await safe_send(
        bot, chat_id, ru.passport_text(view, reasons), reply_markup=keyboards.passport_buttons(settings.public_base_url)
    )


async def send_dictionary(bot: Bot, chat_id: int, session: AsyncSession, season: SeasonDTO | None, today: date) -> None:
    if season is None:
        await safe_send(bot, chat_id, ru.NO_SEASON)
        return
    view = await words.season_dictionary(session, season.id, today=today)
    names = await people.display_names(session, [item.user_id for item in view.user_words], short=True)
    await safe_send(bot, chat_id, ru.dictionary_text(season, view, names), reply_markup=keyboards.word_button())


async def send_facts(
    bot: Bot, chat_id: int, session: AsyncSession, season: SeasonDTO | None, *, is_admin: bool
) -> None:
    if season is None:
        await safe_send(bot, chat_id, ru.NO_SEASON)
        return
    listed = await facts.list_active(session, season.id)
    names = await people.display_names(session, [f.author_id for f in listed if f.author_id is not None], short=True)
    await safe_send(
        bot,
        chat_id,
        ru.facts_text(season, listed, names, with_ids=is_admin),
        reply_markup=keyboards.facts_buttons(is_admin=is_admin, has_facts=bool(listed)),
    )


async def send_journal(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    season: SeasonDTO | None,
    user_id: int,
    today: date,
    settings: Settings,
    media_store: MediaStore,
    *,
    own: bool,
) -> None:
    if season is None:
        await safe_send(bot, chat_id, ru.NO_SEASON)
        return
    view = await journal.build(session, season_id=season.id, user_id=user_id, today=today)
    level = (await passport.build(session, season_id=season.id, user_id=user_id, today=today)).level
    await safe_send(bot, chat_id, ru.journal_text(view, level))
    for item in view.media[:10]:
        # A Mini App upload has no Telegram id until the worker has sent it once; it is on our
        # disk from the start, so it goes as a file. Anything not downloaded yet is skipped.
        if item.tg_file_id is not None:
            photo: str | FSInputFile = item.tg_file_id
        elif item.downloaded:
            photo = FSInputFile(media_store.full_path(item.path))
        else:
            continue
        try:
            await bot.send_photo(chat_id, photo)
        except TelegramAPIError as exc:
            logger.warning(
                "journal_photo_failed", extra={"chat_id": chat_id, "media_id": str(item.media_id), "error": str(exc)}
            )
    if own:
        text = ru.JOURNAL_NOW.format(end=ru.date_genitive(season.ends_on))
        await safe_send(bot, chat_id, text, reply_markup=keyboards.journal_app_button(settings.public_base_url))


async def notify_admin(bot: Bot, admin_chat: int | None, user: UserDTO, text: str) -> None:
    """A one-line note to Mila about what a participant just did; silent for Mila herself."""
    if admin_chat is None or user.id == admin_chat:
        return
    await safe_send(bot, admin_chat, text)
