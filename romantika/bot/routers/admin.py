"""Mila's commands and panel actions (DOMAIN §8). Called from the text and callback routers."""

from __future__ import annotations

from datetime import date, datetime

from aiogram import Bot
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.bot import keyboards
from romantika.bot.send import safe_send
from romantika.config import Settings
from romantika.db import models
from romantika.domain.types import StampLevel
from romantika.services import achievements, content, facts, freezes, links, people, reminders, summary, wishes
from romantika.services.content import SeasonDTO
from romantika.services.people import UserDTO
from romantika.texts import ru


async def show_panel(bot: Bot, chat_id: int, session: AsyncSession, settings: Settings) -> None:
    enabled = await reminders.enabled(session)
    await safe_send(
        bot,
        chat_id,
        ru.PANEL,
        reply_markup=keyboards.panel(reminders_enabled=enabled, public_base_url=settings.public_base_url),
    )


async def names_for(session: AsyncSession, user_ids: list[int]) -> dict[int, str]:
    return await people.display_names(session, user_ids)


async def send_summary(
    bot: Bot, chat_id: int, session: AsyncSession, season: SeasonDTO, week_number: int | None, today: date
) -> None:
    if week_number is None:
        current = await content.current_week(session, season.id, today=today)
        if current is None:
            await safe_send(bot, chat_id, "Сейчас неделя сезона не идёт. Укажи номер: /results 1")
            return
        week_number = current.number
    try:
        report = await summary.week(session, season_id=season.id, week_number=week_number, today=today)
    except content.ContentError:
        await safe_send(bot, chat_id, f"Недели {week_number} в сезоне нет.")
        return
    core = await summary.core(session, season_id=season.id, today=today)
    names = await names_for(session, report.took + list(report.submitted) + core.best)
    await safe_send(bot, chat_id, ru.summary_text(report, names, core))


async def send_core(bot: Bot, chat_id: int, session: AsyncSession, season: SeasonDTO, today: date) -> None:
    core = await summary.core(session, season_id=season.id, today=today)
    breakdowns = await summary.breakdowns_for_season(session, season=season, today=today)
    streaks = {user_id: (b.best_streak, b.current_streak) for user_id, b in breakdowns.items()}
    names = await names_for(session, core.best + core.current)
    await safe_send(bot, chat_id, ru.core_text(core, names, streaks))


async def send_who(bot: Bot, chat_id: int, session: AsyncSession) -> None:
    await safe_send(bot, chat_id, ru.who_text(await people.all_users(session)))


async def send_reminders_now(
    bot: Bot, chat_id: int, session: AsyncSession, season: SeasonDTO, telegram: reminders.MessageSender, now: datetime
) -> None:
    result = await reminders.send(session, season_id=season.id, week_number=None, telegram=telegram, now=now)
    await safe_send(bot, chat_id, result.describe())


async def toggle_reminders(
    bot: Bot, chat_id: int, session: AsyncSession, settings: Settings, *, with_panel: bool
) -> None:
    enabled = not await reminders.enabled(session)
    await reminders.set_enabled(session, enabled)
    if with_panel:
        state = "вкл" if enabled else "выкл"
        text = (
            f"Автонапоминания <b>{state}</b>. Когда включены — четверг 19:00 и воскресенье 12:00, "
            "только тем, кто взялся и не прислал."
        )
        await safe_send(
            bot,
            chat_id,
            text,
            reply_markup=keyboards.panel(reminders_enabled=enabled, public_base_url=settings.public_base_url),
        )
    else:
        await safe_send(bot, chat_id, ru.reminders_toggled(enabled))


async def give_achievement(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    season: SeasonDTO,
    target: UserDTO,
    code_or_text: str,
    actor_id: int,
    now: datetime,
) -> None:
    result = await achievements.award(
        session, season_id=season.id, user_id=target.id, code_or_text=code_or_text, awarded_by=actor_id, now=now
    )
    name = target.display_name_with_username
    if not result.created:
        await safe_send(bot, chat_id, f"У {ru.escape(name)} такая уже есть: {ru.escape(result.label)}")
        return
    await safe_send(bot, target.id, ru.achievement_given(result.label))
    await safe_send(bot, chat_id, f"Выдала {ru.escape(result.label)} → {ru.escape(name)}")


async def give_freeze(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    settings: Settings,
    season: SeasonDTO,
    target: UserDTO,
    reason: str,
    actor_id: int,
    now: datetime,
) -> None:
    granted = await freezes.grant(
        session,
        season_id=season.id,
        user_id=target.id,
        reason=models.FreezeReason(reason),
        granted_by=actor_id,
        now=now,
    )
    panel = keyboards.panel(
        reminders_enabled=await reminders.enabled(session), public_base_url=settings.public_base_url
    )
    name = ru.escape(target.display_name_with_username)
    if granted:
        await safe_send(bot, target.id, ru.freeze_given(reason))
        total = await freezes.total(session, season.id, target.id)
        await safe_send(bot, chat_id, f"Выдала. Теперь у {name} заморозок: {total}", reply_markup=panel)
    else:
        await safe_send(bot, chat_id, f"У {name} уже потолок — {season.max_freezes} заморозок.", reply_markup=panel)


async def save_wish(
    bot: Bot, chat_id: int, session: AsyncSession, season: SeasonDTO, target_id: int, text: str, now: datetime
) -> None:
    await wishes.set_wish(session, season_id=season.id, user_id=target_id, text=text, now=now)
    target = await people.get_user(session, target_id)
    await safe_send(
        bot,
        chat_id,
        f"Записала для {ru.escape(ru.display_name(target, target_id))}. Попадёт в его журнал в конце сезона.",
    )


async def resolve_target(session: AsyncSession, message: Message, argument: str) -> tuple[UserDTO | None, str]:
    """Who a reply-based admin command is about: the linked author, or the first word of the argument."""
    replied = message.reply_to_message
    if replied is not None:
        link = await links.lookup(session, admin_chat_id=message.chat.id, admin_message_id=replied.message_id)
        if link is not None:
            # A nick typed alongside the reply is redundant, not part of the badge text.
            if argument.startswith("@"):
                argument = argument.partition(" ")[2].strip()
            return await people.get_user(session, link.user_id), argument
    if argument:
        head, _, rest = argument.partition(" ")
        return await people.find(session, head), rest.strip()
    return None, argument


async def run_command(
    action: str,
    argument: str,
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    user: UserDTO,
    season: SeasonDTO | None,
    admin_chat: int | None,
    now: datetime,
    today: date,
) -> None:
    chat_id = message.chat.id
    if season is None:
        await safe_send(bot, chat_id, ru.NO_SEASON)
        return

    if action == "results":
        number = int(argument) if argument.isdigit() else None
        await send_summary(bot, chat_id, session, season, number, today)
    elif action == "core":
        await send_core(bot, chat_id, session, season, today)
    elif action == "remind":
        from romantika.bot.gateway import AiogramTelegramGateway

        await send_reminders_now(bot, chat_id, session, season, AiogramTelegramGateway(bot), now)
    elif action == "badges":
        await safe_send(bot, chat_id, ru.badges_text(await achievements.catalogue(session, season.id)))
    elif action == "badge":
        target, rest = await resolve_target(session, message, argument)
        if target is None:
            await safe_send(
                bot,
                chat_id,
                "Не поняла, кому. Ответь этой командой на присланный отчёт или укажи ник: "
                "<code>/ачивка @ksu повар</code>\n\nСписок: /badges",
            )
            return
        if not rest:
            await safe_send(bot, chat_id, "Не поняла, какую. Список: /badges")
            return
        await give_achievement(bot, chat_id, session, season, target, rest, user.id, now)
    elif action == "reminders":
        await toggle_reminders(bot, chat_id, session, settings, with_panel=False)
    elif action == "who":
        await send_who(bot, chat_id, session)
    elif action == "wish":
        target, rest = await resolve_target(session, message, argument)
        if target is None or not rest:
            await safe_send(
                bot,
                chat_id,
                "Так: <code>/пожелание @ksu текст</code> или ответом на отчёт: <code>/пожелание текст</code>",
            )
            return
        await save_wish(bot, chat_id, session, season, target.id, rest, now)
    elif action == "fact":
        if not argument:
            await safe_send(bot, chat_id, "Так: <code>/факт текст</code>")
            return
        week = await content.current_week(session, season.id, today=today)
        await facts.add(
            session, season_id=season.id, week_id=week.id if week else None, text=argument, author_id=None, now=now
        )
        total = len(await facts.list_active(session, season.id))
        await safe_send(
            bot,
            chat_id,
            f"Записала. Фактов за сезон: <b>{total}</b>",
            reply_markup=keyboards.facts_buttons(is_admin=True, has_facts=True),
        )
    elif action == "fact_remove":
        if not argument.isdigit():
            await safe_send(bot, chat_id, "Так: <code>/факт- номер</code> (номер виден в списке /факты)")
            return
        removed = await facts.remove(session, fact_id=int(argument), actor_id=user.id, now=now)
        left = len(await facts.list_active(session, season.id))
        await safe_send(
            bot,
            chat_id,
            f"Убрала. Осталось: {left}" if removed else "Такого факта нет.",
            reply_markup=keyboards.facts_buttons(is_admin=True, has_facts=left > 0),
        )
    else:
        await safe_send(bot, chat_id, ru.UNKNOWN_COMMAND)


def level_from(value: str) -> StampLevel:
    return StampLevel(value)
