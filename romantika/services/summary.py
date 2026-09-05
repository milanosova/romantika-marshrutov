"""Weekly summary, core and the draft of the Sunday post (DOMAIN §5, §8).

The core is counted twice on purpose: by the best streak of the season (as in legacy, it
never falls) and by the current one («в строю сейчас»). Which one is the headline number is
Mila's call, so both travel in the summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.domain import rules
from romantika.domain.calendar import to_moscow
from romantika.domain.types import Breakdown, StampLevel
from romantika.services import content, freezes, people, stamps
from romantika.services.content import SeasonDTO, WeekDTO

#: A participant is in the core from two stamped weeks in a row (DOMAIN §5).
CORE_STREAK = 2

#: The quotes of the draft post are cut to this many characters, as in legacy.
QUOTE_LENGTH = 120
RULE = "━━━━━━━━━━"

#: Intents that mean «I am doing this week» and therefore get a reminder.
TAKING = (models.IntentChoice.TAKE, models.IntentChoice.TRY)


@dataclass(frozen=True, slots=True)
class WeekSummary:
    """The admin summary of one week."""

    week_number: int
    week_title: str
    members_total: int
    reports_total: int
    took: list[int]
    submitted: dict[int, StampLevel]
    took_not_submitted: list[int]
    core_best: int
    core_current: int


@dataclass(frozen=True, slots=True)
class CoreView:
    """Who is in the core, by the best streak of the season and by the current one."""

    best: list[int]
    current: list[int]


async def week(session: AsyncSession, *, season_id: int, week_number: int, today: date) -> WeekSummary:
    """Numbers for «сводка недели»: who took the week, who submitted, who went quiet."""
    season = await content.require_season(session, season_id)
    target = await _require_week(session, season_id, week_number)

    intents = await people.intents(session, season_id=season_id, week_id=target.id)
    submitted = await stamps.for_week(session, season_id=season_id, week_id=target.id)
    took = sorted(user_id for user_id, choice in intents.items() if choice in TAKING)
    core = await _core(session, season=season, today=today)

    return WeekSummary(
        week_number=target.number,
        week_title=target.title,
        members_total=len(await people.members(session, season_id)),
        reports_total=await _report_count(session, season_id=season_id, week_id=target.id),
        took=took,
        submitted=submitted,
        took_not_submitted=[user_id for user_id in took if user_id not in submitted],
        core_best=len(core.best),
        core_current=len(core.current),
    )


async def reminder_recipients(session: AsyncSession, *, season_id: int, week_number: int) -> list[int]:
    """Those who pressed «Берусь/Попробую» and have no stamp yet (DOMAIN §8)."""
    target = await _require_week(session, season_id, week_number)
    intents = await people.intents(session, season_id=season_id, week_id=target.id)
    submitted = await stamps.for_week(session, season_id=season_id, week_id=target.id)
    return sorted(user_id for user_id, choice in intents.items() if choice in TAKING and user_id not in submitted)


async def core(session: AsyncSession, *, season_id: int, today: date) -> CoreView:
    """Participants with a streak of at least two weeks, best streak first."""
    season = await content.require_season(session, season_id)
    return await _core(session, season=season, today=today)


@dataclass(frozen=True, slots=True)
class DraftPost:
    """The Sunday post to copy, and what Mila should know but must not paste."""

    week_number: int
    week_title: str
    text: str
    notes: list[str]

    def as_message(self) -> str:
        """One bot message: the post between rules, the remarks under it."""
        head = f"Черновик «Привала» · неделя {self.week_number} · {self.week_title}"
        parts = [head, "", RULE, "", self.text, "", RULE]
        if self.notes:
            parts += [""] + [f"Не для поста: {note}" for note in self.notes]
        return "\n".join(parts)


async def draft_post(session: AsyncSession, *, season_id: int, week_number: int, today: date) -> DraftPost:
    """The Sunday «Привал» post, ready to copy and finish by hand (DOMAIN §8).

    Square brackets are Mila's placeholders: the bot cannot know what she made this week and
    who wrote in the channel comments, so it says so instead of inventing it. The Russian
    text of the template lives here and not in `romantika/texts/` because it is admin
    scaffolding, not a message to a participant.
    """
    season = await content.require_season(session, season_id)
    target = await _require_week(session, season_id, week_number)
    if today < target.starts_on:
        note = f"неделя ещё не началась, откроется {target.starts_on:%d.%m} — черновик появится вместе с ней"
        return DraftPost(week_number=target.number, week_title=target.title, text="", notes=[note])
    submitted = await stamps.for_week(session, season_id=season_id, week_id=target.id)
    quotes = await _quotes(session, season_id=season_id, week_id=target.id)
    names = await _names(session, season_id)

    lines = [
        f"Привал. Неделя «{target.title}» закончилась.",
        "",
        "[ЗДЕСЬ ТВОЁ: фото своего результата и что не получилось. Обязательно, даже если не прислал никто]",
        "",
    ]
    if submitted:
        lines.append("На этой неделе задание сделали:")
        for user_id in sorted(submitted, key=lambda uid: names.get(uid, str(uid)).lower()):
            mark = "⭐ " if submitted[user_id] is StampLevel.MAX else "· "
            quote = _clip(quotes.get(user_id, ""), QUOTE_LENGTH)
            lines.append(f"{mark}{names.get(user_id, str(user_id))}" + (f" — {quote}" if quote else ""))
    else:
        lines.append("[Пока никто не прислал в бота]")

    lines += ["", "[+ ДОБАВИТЬ ТЕХ, КТО НАПИСАЛ В КОММЕНТАРИЯХ — бот их не видит]", ""]
    if target.word:
        lines += [f"Слово недели: {target.word} — {target.word_meaning}.", ""]
    lines += [
        "[ЗАКРЫВАЮЩИЙ ВОПРОС — про предмет перед глазами, а не про чувства]",
        "",
        f"#маршрут_итоги {season.hashtag}".strip(),
    ]

    intents = await people.intents(session, season_id=season_id, week_id=target.id)
    silent = [user_id for user_id, choice in sorted(intents.items()) if choice in TAKING and user_id not in submitted]
    notes: list[str] = []
    if silent:
        joined = ", ".join(names.get(user_id, str(user_id)) for user_id in silent)
        notes.append(f"взялись и не прислали — {joined}")
    if today < target.ends_on:
        notes.append(f"неделя ещё идёт, заканчивается {target.ends_on:%d.%m}.")
    return DraftPost(week_number=target.number, week_title=target.title, text="\n".join(lines), notes=notes)


async def _core(session: AsyncSession, *, season: SeasonDTO, today: date) -> CoreView:
    """Both core numbers from one pass over the season."""
    breakdowns = await breakdowns_for_season(session, season=season, today=today)
    best = rules.core_members(breakdowns, CORE_STREAK)
    current = sorted(
        (user_id for user_id, breakdown in breakdowns.items() if breakdown.current_streak >= CORE_STREAK),
        key=lambda user_id: (-breakdowns[user_id].current_streak, user_id),
    )
    return CoreView(best=best, current=current)


async def breakdowns_for_season(session: AsyncSession, *, season: SeasonDTO, today: date) -> dict[int, Breakdown]:
    """`{user_id: Breakdown}` for every member, with a constant number of queries."""
    weeks = [week_dto.info for week_dto in await content.weeks(session, season.id)]
    joined = await people.members(session, season.id)
    all_stamps = await stamps.for_season(session, season.id)
    bonuses = await freezes.bonus_counts(session, season.id)
    return {
        user_id: rules.season_breakdown(
            weeks=weeks,
            stamps=all_stamps.get(user_id, {}),
            bonus_freezes=bonuses.get(user_id, 0),
            base_freezes=season.base_freezes,
            max_freezes=season.max_freezes,
            joined_on=to_moscow(joined_at).date(),
            today=today,
        )
        for user_id, joined_at in joined.items()
    }


async def _require_week(session: AsyncSession, season_id: int, week_number: int) -> WeekDTO:
    target = await content.week_by_number(session, season_id, week_number)
    if target is None:
        raise content.ContentError(f"season {season_id} has no week {week_number}")
    return target


async def _report_count(session: AsyncSession, *, season_id: int, week_id: int) -> int:
    query = (
        select(func.count())
        .select_from(models.Report)
        .where(
            models.Report.season_id == season_id,
            models.Report.week_id == week_id,
            models.Report.deleted_at.is_(None),
        )
    )
    return int((await session.execute(query)).scalar_one())


def _clip(text: str, limit: int) -> str:
    """Cut at a word with an ellipsis, so Mila sees the quote goes on."""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return cut.rstrip(" ,;:—-") + "…"


async def _quotes(session: AsyncSession, *, season_id: int, week_id: int) -> dict[int, str]:
    """The first line each participant wrote that week, as in legacy's draft post."""
    query = (
        select(models.Report.user_id, models.Report.text)
        .where(
            models.Report.season_id == season_id,
            models.Report.week_id == week_id,
            models.Report.deleted_at.is_(None),
            models.Report.text.is_not(None),
            models.Report.text != "",
        )
        .order_by(models.Report.created_at, models.Report.id)
    )
    quotes: dict[int, str] = {}
    for user_id, text in (await session.execute(query)).all():
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if first:
            quotes.setdefault(user_id, first)
    return quotes


async def _names(session: AsyncSession, season_id: int) -> dict[int, str]:
    """Display names of the season's members, for the admin texts."""
    query = (
        select(models.User)
        .join(models.SeasonMember, models.SeasonMember.user_id == models.User.id)
        .where(models.SeasonMember.season_id == season_id)
    )
    return {row.id: people.to_dto(row).display_name for row in (await session.execute(query)).scalars()}
