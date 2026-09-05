"""Commands, reply-keyboard buttons and answers to the bot's questions (dialog states)."""

from __future__ import annotations

import logging
from datetime import date, datetime

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.bot import keyboards
from romantika.bot.keyboards import button_action
from romantika.bot.routers import admin, common
from romantika.bot.send import safe_send
from romantika.config import Settings
from romantika.services import content, facts, letters, links, people, words
from romantika.services.content import SeasonDTO
from romantika.services.media import MediaStore
from romantika.services.people import DialogStateDTO, UserDTO
from romantika.texts import ru

logger = logging.getLogger(__name__)

COMMAND_ALIASES: dict[str, str] = {
    "/start": "start",
    "/help": "help",
    "/помощь": "help",
    "/whoami": "whoami",
    "/task": "task",
    "/today": "today",
    "/passport": "passport",
    "/words": "words",
    "/facts": "facts",
    "/факты": "facts",
    "/journal": "journal",
    "/журнал": "journal",
    # admin
    "/results": "results",
    "/core": "core",
    "/remind": "remind",
    "/badges": "badges",
    "/ачивки": "badges",
    "/badge": "badge",
    "/ачивка": "badge",
    "/reminders": "reminders",
    "/who": "who",
    "/wish": "wish",
    "/пожелание": "wish",
    "/fact-": "fact_remove",
    "/факт-": "fact_remove",
    "/fact": "fact",
    "/факт": "fact",
}
ADMIN_ACTIONS = {
    "results",
    "core",
    "remind",
    "badges",
    "badge",
    "reminders",
    "who",
    "wish",
    "fact",
    "fact_remove",
    "admin",
}


def parse_command(text: str) -> tuple[str | None, str]:
    """`/results 2` → ("results", "2"); handles `/cmd@bot` and Russian aliases."""
    head, _, tail = text.partition(" ")
    head = head.split("@", 1)[0].lower()
    action = COMMAND_ALIASES.get(head)
    if action is None:
        # `/факт-3` without a space
        for alias, name in COMMAND_ALIASES.items():
            if alias.endswith("-") and head.startswith(alias):
                return name, (head[len(alias) :] + " " + tail).strip()
    return action, tail.strip()


async def handle_text(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    user: UserDTO,
    season: SeasonDTO | None,
    is_admin: bool,
    admin_chat: int | None,
    dialog: DialogStateDTO | None,
    now: datetime,
    today: date,
    media_store: MediaStore,
) -> None:
    text = (message.text or "").strip()
    chat_id = message.chat.id
    keyboard = keyboards.main_keyboard(is_admin=is_admin)
    action: str | None
    argument = ""
    if text.startswith("/"):
        action, argument = parse_command(text)
        if action is None:
            await people.clear_dialog_state(session, user.id)
            await safe_send(bot, chat_id, ru.UNKNOWN_COMMAND, reply_markup=keyboard)
            return
    else:
        action = button_action(text)

    if action is None:
        if dialog is not None:
            await people.clear_dialog_state(session, user.id)
            await answer_dialog(message, bot, session, settings, user, season, is_admin, admin_chat, dialog, now, today)
            return
        raise SkipHandler  # a report

    if dialog is not None:
        await people.clear_dialog_state(session, user.id)

    if action in ADMIN_ACTIONS and not is_admin:
        await safe_send(bot, chat_id, ru.NOT_ADMIN, reply_markup=keyboard)
        return

    if action == "start":
        await safe_send(bot, chat_id, ru.greeting(season) + ru.GREETING_CTA, reply_markup=keyboard)
    elif action == "help":
        await safe_send(bot, chat_id, ru.HELP, reply_markup=keyboard)
        if is_admin:
            await safe_send(bot, chat_id, ru.ADMIN_MEMO)
    elif action == "whoami":
        await safe_send(bot, chat_id, ru.WHOAMI.format(user_id=user.id))
    elif action == "more":
        await safe_send(bot, chat_id, ru.MORE_MENU, reply_markup=keyboards.more_menu(settings.public_base_url))
    elif action == "task":
        await common.send_task(bot, chat_id, session, season, today)
    elif action == "today":
        await common.send_today(bot, chat_id, session, season, today, settings)
    elif action == "passport":
        await common.send_passport(bot, chat_id, session, season, user.id, today, settings)
    elif action == "words":
        await common.send_dictionary(bot, chat_id, session, season, today)
    elif action == "facts":
        await common.send_facts(bot, chat_id, session, season, is_admin=is_admin)
    elif action == "journal":
        target = user.id
        if is_admin and argument:
            found = await people.find(session, argument)
            if found is None:
                await safe_send(bot, chat_id, "Не нашла такого. Список: /who")
                return
            target = found.id
        await common.send_journal(
            bot, chat_id, session, season, target, today, settings, media_store, own=target == user.id
        )
    elif action == "write":
        await people.set_dialog_state(session, user.id, "letter", now=now)
        await safe_send(bot, chat_id, ru.WRITE_PROMPT)
    elif action == "admin":
        await admin.show_panel(bot, chat_id, session, settings)
    else:
        await admin.run_command(action, argument, message, bot, session, settings, user, season, admin_chat, now, today)


async def answer_dialog(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    user: UserDTO,
    season: SeasonDTO | None,
    is_admin: bool,
    admin_chat: int | None,
    dialog: DialogStateDTO,
    now: datetime,
    today: date,
) -> None:
    text = (message.text or "").strip()
    chat_id = message.chat.id
    keyboard = keyboards.main_keyboard(is_admin=is_admin)
    author = user.display_name_with_username

    if dialog.state == "word":
        if season is None:
            await safe_send(bot, chat_id, ru.NO_SEASON, reply_markup=keyboard)
            return
        week = await content.current_week(session, season.id, today=today)
        result = await words.add(
            session, season_id=season.id, user_id=user.id, week_id=week.id if week else None, raw=text, now=now
        )
        await safe_send(
            bot, chat_id, ru.WORD_SAVED + (ru.WORD_FREEZE_BONUS if result.freeze_granted else ""), reply_markup=keyboard
        )
        await common.notify_admin(
            bot, admin_chat, user, ru.admin_word_added(author, text, week.number if week else None)
        )
        return

    if dialog.state == "letter":
        letter = await letters.create(
            session,
            season_id=season.id if season else None,
            user_id=user.id,
            source=letters.Source.BOT,
            text=text,
            now=now,
        )
        if admin_chat is not None and user.id != admin_chat:
            head = await safe_send(bot, admin_chat, ru.admin_letter_header(author, text))
            if head is not None:
                await links.remember(
                    session,
                    admin_chat_id=admin_chat,
                    admin_message_id=head.message_id,
                    user_id=user.id,
                    report_id=None,
                    week_id=None,
                    letter_id=letter.id,
                    now=now,
                )
        await safe_send(bot, chat_id, ru.LETTER_SENT, reply_markup=keyboard)
        return

    if dialog.state == "fact":
        if season is None:
            await safe_send(bot, chat_id, ru.NO_SEASON, reply_markup=keyboard)
            return
        week = await content.current_week(session, season.id, today=today)
        await facts.add(
            session,
            season_id=season.id,
            week_id=week.id if week else None,
            text=text,
            author_id=None if is_admin else user.id,
            now=now,
        )
        if is_admin:
            total = len(await facts.list_active(session, season.id))
            await safe_send(
                bot,
                chat_id,
                f"Записала. Фактов за сезон: <b>{total}</b>",
                reply_markup=keyboards.facts_buttons(is_admin=True, has_facts=True),
            )
        else:
            await safe_send(bot, chat_id, ru.FACT_SAVED, reply_markup=keyboard)
            await common.notify_admin(
                bot, admin_chat, user, ru.admin_fact_added(author, text, week.number if week else None)
            )
        return

    if dialog.state == "edit" and is_admin and season is not None:
        week_number = int(dialog.payload["week_number"])
        field = str(dialog.payload["field"])
        week = await content.week_by_number(session, season.id, week_number)
        if week is None:
            await safe_send(bot, chat_id, "Такой недели нет.")
            return
        try:
            updated = await content.update_week(
                session, actor_id=user.id, week_id=week.id, changes={field: text}, today=today
            )
        except content.ContentError:
            # The panel never offers a finished week, but the dialog outlives midnight: without
            # this the whole update transaction would roll back and Mila would get no answer.
            await safe_send(bot, chat_id, ru.WEEK_ALREADY_OVER)
            return
        await safe_send(
            bot,
            chat_id,
            f"Готово. Неделя {week_number} теперь так:\n\n{ru.task_text(updated)}\n\n"
            "<i>Правка сохранена в базе и в журнале изменений.</i>",
        )
        await admin.show_panel(bot, chat_id, session, settings)
        return

    if dialog.state == "wish" and is_admin and season is not None:
        target = int(dialog.payload["user_id"])
        await admin.save_wish(bot, chat_id, session, season, target, text, now)
        return

    logger.warning("dialog_state_unhandled", extra={"state": dialog.state, "user_id": user.id})
    raise SkipHandler


def build() -> Router:
    router = Router(name="user")
    router.message.register(handle_text, F.text)
    return router
