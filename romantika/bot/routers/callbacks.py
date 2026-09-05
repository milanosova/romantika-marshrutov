"""Inline buttons of participants and of the admin panel."""

from __future__ import annotations

import logging
from datetime import date, datetime

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.bot import keyboards
from romantika.bot.routers import admin, common
from romantika.bot.send import safe_send
from romantika.config import Settings
from romantika.db import models
from romantika.domain.types import StampLevel
from romantika.services import achievements, content, facts, letters, links, people, reports
from romantika.services.content import SeasonDTO
from romantika.services.gateways import TelegramGateway
from romantika.services.media import MediaStore
from romantika.services.people import UserDTO
from romantika.texts import ru

logger = logging.getLogger(__name__)


async def answer(query: CallbackQuery, text: str | None = None, *, alert: bool = False) -> None:
    try:
        await query.answer(text=text, show_alert=alert)
    except TelegramAPIError as exc:  # the button may be older than Telegram allows to answer
        logger.info("callback_answer_failed", extra={"error": str(exc)})


async def handle_callback(
    query: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    user: UserDTO,
    season: SeasonDTO | None,
    is_admin: bool,
    admin_chat: int | None,
    now: datetime,
    today: date,
    telegram: TelegramGateway,
    media_store: MediaStore,
) -> None:
    data = query.data or ""
    chat_id = query.message.chat.id if query.message is not None else user.id
    parts = data.split(":")
    head = parts[0]
    try:
        await _dispatch(
            parts,
            head,
            query,
            bot,
            chat_id,
            session,
            settings,
            user,
            season,
            is_admin,
            admin_chat,
            now,
            today,
            telegram,
            media_store,
        )
    except (ValueError, KeyError, IndexError) as exc:
        # Data no keyboard of ours produces (an old client, a forged button): answer, do not crash.
        logger.warning("callback_malformed", extra={"data": data, "error": str(exc)})
        await answer(query, "Кнопка устарела — открой экран заново.")


async def _dispatch(
    parts: list[str],
    head: str,
    query: CallbackQuery,
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    settings: Settings,
    user: UserDTO,
    season: SeasonDTO | None,
    is_admin: bool,
    admin_chat: int | None,
    now: datetime,
    today: date,
    telegram: TelegramGateway,
    media_store: MediaStore,
) -> None:

    # A button ends whatever the bot was waiting for (DOMAIN §10.8): otherwise a stale «пиши
    # письмо» swallows the next real report — no row, no stamp. The handlers that open a new
    # dialog (addword, addfact, more:write, adm:field, adm:wish) set their state below.
    await people.clear_dialog_state(session, user.id)

    if head == "adm":
        if not is_admin:
            await answer(query, "Это кнопки Милы.")
            return
        await answer(query)
        if season is None:
            await safe_send(bot, chat_id, ru.NO_SEASON)
            return
        await handle_admin(parts[1:], query, bot, chat_id, session, settings, user, season, now, today, telegram)
        return

    if season is None:
        await answer(query)
        await safe_send(bot, chat_id, ru.NO_SEASON)
        return

    if head == "intent" and len(parts) == 3:
        week_number, choice = int(parts[1]), parts[2]
        week = await content.week_by_number(session, season.id, week_number)
        if week is None or choice not in ru.INTENT_HINTS:
            await answer(query)
            return
        await people.set_intent(
            session, season_id=season.id, user_id=user.id, week_id=week.id, choice=models.IntentChoice(choice), now=now
        )
        await answer(query, ru.INTENT_HINTS[choice], alert=True)
        await common.notify_admin(
            bot,
            admin_chat,
            user,
            f"👤 {ru.escape(user.display_name_with_username)} — "
            f"<b>{ru.INTENT_NAMES[choice]}</b> на неделе {week_number}",
        )
        return

    if head == "level" and len(parts) == 3:
        week_number, level = int(parts[1]), StampLevel(parts[2])
        result = await reports.fix_level(
            session, season_id=season.id, user_id=user.id, week_number=week_number, level=level, now=now
        )
        if result.ok:
            await answer(query, f"Поправила: теперь {ru.level_name(level)}", alert=True)
            await safe_send(bot, chat_id, f"Поправила — засчитано как <b>{ru.level_name(level)}</b>.")
        elif result.reason == reports.NO_DOWNGRADE:
            await answer(query, "Максимум не понижаю — звёздочка остаётся ⭐", alert=True)
        else:
            await answer(query, "За эту неделю отчёта нет — пришли текст или фото.", alert=True)
        return

    if head == "notreport" and len(parts) == 2:
        report_id = int(parts[1])
        cancelled = await reports.cancel(session, user_id=user.id, report_id=report_id, now=now)
        await answer(query)
        if not cancelled.ok:
            already = cancelled.reason == "already_cancelled"
            await safe_send(bot, chat_id, ru.NOT_REPORT_ALREADY if already else ru.NOT_REPORT_FOREIGN)
            return
        row = await session.get(models.Report, report_id)
        # A message sent outside a week is a letter already; taking it back adds nothing new.
        letter = await letters.for_report(session, report_id) or await letters.create(
            session,
            season_id=season.id,
            user_id=user.id,
            source=letters.Source.NOT_REPORT,
            text=row.text if row else None,
            report_id=report_id,
            now=now,
        )
        if admin_chat is not None and user.id != admin_chat:
            head_message = await safe_send(
                bot,
                admin_chat,
                ru.admin_letter_header(user.display_name_with_username, row.text if row else None, corrected=True),
            )
            if head_message is not None:
                await links.remember(
                    session,
                    admin_chat_id=admin_chat,
                    admin_message_id=head_message.message_id,
                    user_id=user.id,
                    report_id=report_id,
                    week_id=None,
                    letter_id=letter.id,
                    now=now,
                )
        await safe_send(bot, chat_id, ru.NOT_REPORT_DONE, reply_markup=keyboards.main_keyboard(is_admin=is_admin))
        return

    if head == "more" and len(parts) == 2:
        await answer(query)
        if parts[1] == "journal":
            await common.send_journal(bot, chat_id, session, season, user.id, today, settings, media_store, own=True)
        elif parts[1] == "write":
            await people.set_dialog_state(session, user.id, "letter", now=now)
            await safe_send(bot, chat_id, ru.WRITE_PROMPT)
        elif parts[1] == "help":
            await safe_send(bot, chat_id, ru.HELP, reply_markup=keyboards.main_keyboard(is_admin=is_admin))
        return

    if head == "endofseason":
        await answer(query)
        await safe_send(bot, chat_id, ru.end_of_season_text(season), reply_markup=keyboards.journal_button())
        return

    if head == "journal":
        await answer(query)
        await common.send_journal(bot, chat_id, session, season, user.id, today, settings, media_store, own=True)
        return

    if head == "addword":
        await people.set_dialog_state(session, user.id, "word", now=now)
        await answer(query)
        await safe_send(bot, chat_id, ru.WORD_PROMPT)
        return

    if head == "addfact":
        await people.set_dialog_state(session, user.id, "fact", now=now)
        await answer(query)
        await safe_send(bot, chat_id, ru.FACT_PROMPT)
        return

    await answer(query)


async def handle_admin(
    parts: list[str],
    query: CallbackQuery,
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    settings: Settings,
    user: UserDTO,
    season: SeasonDTO,
    now: datetime,
    today: date,
    telegram: TelegramGateway,
) -> None:
    if not parts:
        return
    action = parts[0]

    if action == "panel":
        await admin.show_panel(bot, chat_id, session, settings)
    elif action == "draft":
        from romantika.services import summary

        current = await content.current_week(session, season.id, today=today)
        if current is None:
            await safe_send(bot, chat_id, "Сейчас неделя сезона не идёт.")
            return
        await safe_send(
            bot,
            chat_id,
            (
                await summary.draft_post(session, season_id=season.id, week_number=current.number, today=today)
            ).as_message(),
        )
    elif action == "summary":
        await admin.send_summary(bot, chat_id, session, season, None, today)
    elif action == "core":
        await admin.send_core(bot, chat_id, session, season, today)
    elif action == "who":
        await admin.send_who(bot, chat_id, session)
    elif action == "remind":
        await admin.send_reminders_now(bot, chat_id, session, season, telegram, now)
    elif action == "toggle":
        await admin.toggle_reminders(bot, chat_id, session, settings, with_panel=True)
    elif action == "people" and len(parts) == 3:
        prefix, page = parts[1], int(parts[2])
        prompt = {"badge": "Кому выдаём?", "freeze": "Кому даём заморозку?", "wish": "Кому пишем пожелание?"}.get(
            prefix, "Кому?"
        )
        everyone = await people.all_users(session)
        await safe_send(
            bot,
            chat_id,
            prompt,
            reply_markup=keyboards.people_list(everyone, prefix=prefix, page=page, exclude=user.id),
        )
    elif action == "badge" and len(parts) == 2:
        target = await people.get_user(session, int(parts[1]))
        if target is None:
            return
        catalogue = await achievements.catalogue(session, season.id)
        await safe_send(
            bot,
            chat_id,
            f"Какую ачивку для {ru.escape(target.display_name_with_username)}?",
            reply_markup=keyboards.achievement_choices(target.id, catalogue),
        )
    elif action == "give" and len(parts) >= 3:
        target = await people.get_user(session, int(parts[1]))
        if target is None:
            return
        await admin.give_achievement(bot, chat_id, session, season, target, ":".join(parts[2:]), user.id, now)
    elif action == "freeze" and len(parts) == 2:
        target = await people.get_user(session, int(parts[1]))
        if target is None:
            return
        await safe_send(
            bot,
            chat_id,
            f"За что даём {ru.escape(target.display_name_with_username)}?",
            reply_markup=keyboards.freeze_reasons(target.id),
        )
    elif action == "frz" and len(parts) == 3:
        target = await people.get_user(session, int(parts[1]))
        if target is None:
            return
        await admin.give_freeze(bot, chat_id, session, settings, season, target, parts[2], user.id, now)
    elif action == "wish" and len(parts) == 2:
        target = await people.get_user(session, int(parts[1]))
        if target is None:
            return
        await people.set_dialog_state(session, user.id, "wish", {"user_id": target.id}, now=now)
        await safe_send(
            bot,
            chat_id,
            f"Напиши пожелание для {ru.escape(target.display_name_with_username)} одним сообщением. "
            "Попадёт в его журнал в конце сезона.",
        )
    elif action == "edit":
        weeks = [week for week in await content.weeks(session, season.id) if week.ends_on >= today]
        await safe_send(
            bot,
            chat_id,
            "Какую неделю правим?\n\n<i>▶ — идёт сейчас, 🔒 — ещё закрыта. "
            "Прошедшие не показываю: люди их уже прожили, задним числом не меняем.</i>",
            reply_markup=keyboards.week_choices(weeks, today=today),
        )
    elif action == "week" and len(parts) == 2:
        week = await content.week_by_number(session, season.id, int(parts[1]))
        if week is None:
            return
        lines = [f"<b>Неделя {week.number} · {ru.escape(week.title)}</b>", ""]
        for field, label in ru.WEEK_FIELDS.items():
            value = getattr(week, field) or "—"
            lines.append(f"<b>{label}:</b> {ru.escape(value[:160])}")
        await safe_send(
            bot, chat_id, "\n".join(lines) + "\n\nЧто меняем?", reply_markup=keyboards.field_choices(week.number)
        )
    elif action == "field" and len(parts) == 3:
        week = await content.week_by_number(session, season.id, int(parts[1]))
        field = parts[2]
        if week is None or field not in ru.WEEK_FIELDS:
            return
        await people.set_dialog_state(session, user.id, "edit", {"week_number": week.number, "field": field}, now=now)
        current_value = getattr(week, field) or "—"
        await safe_send(
            bot,
            chat_id,
            f"<b>{ru.WEEK_FIELDS[field]}</b> недели {week.number}\n\n"
            f"Сейчас: <i>{ru.escape(current_value)}</i>\n\nПришли новый текст одним сообщением.",
        )
    elif action == "delfact" and len(parts) == 1:
        listed = await facts.list_active(session, season.id)
        if not listed:
            await safe_send(bot, chat_id, "Фактов пока нет.")
            return
        await safe_send(bot, chat_id, "Какой убрать?", reply_markup=keyboards.fact_choices(listed))
    elif action == "delfact" and len(parts) == 2:
        removed = await facts.remove(session, fact_id=int(parts[1]), actor_id=user.id, now=now)
        left = len(await facts.list_active(session, season.id))
        await safe_send(
            bot,
            chat_id,
            f"Убрала. Осталось: {left}" if removed else "Такого факта уже нет.",
            reply_markup=keyboards.facts_buttons(is_admin=True, has_facts=left > 0),
        )
    else:
        logger.info("admin_callback_unknown", extra={"data": ":".join(parts)})


def build() -> Router:
    router = Router(name="callbacks")
    router.callback_query.register(handle_callback)
    return router
