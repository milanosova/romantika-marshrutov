"""Assembling API responses from service view models (no business logic here)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.config import Settings
from romantika.db import models
from romantika.domain.calendar import to_moscow
from romantika.domain.types import WeekState
from romantika.domain.tzolkin import tzolkin_day
from romantika.services import content, freezes, journal, passport, people, reports
from romantika.services.content import SeasonDTO, WeekDTO
from romantika.services.journal import ReportDTO
from romantika.services.passport import PassportView
from romantika.texts import ru
from romantika.web import schemas


def season_out(season: SeasonDTO) -> schemas.SeasonOut:
    return schemas.SeasonOut(
        id=season.id,
        slug=season.slug,
        title=season.title,
        title_accusative=season.title_accusative,
        starts_on=season.starts_on,
        ends_on=season.ends_on,
        daily_kind=season.daily_kind,
        daily_note=season.daily_note,
    )


def passport_out(view: PassportView, reasons: list[str]) -> schemas.PassportOut:
    b = view.breakdown
    return schemas.PassportOut(
        stamps=b.stamps,
        stamps_max=view.stamps_max,
        weeks_total=view.weeks_total,
        freezes_used=b.freezes_used,
        freezes_left=b.freezes_left,
        freezes_total=b.freezes_total,
        best_streak=b.best_streak,
        current_streak=b.current_streak,
        level=view.level.value if view.level else None,
        freeze_reasons=reasons,
    )


def week_out(week: WeekDTO, state: WeekState, level: str | None, *, reveal: bool) -> schemas.WeekOut:
    """Future weeks travel with their calendar only: no task texts before the week starts."""
    base = schemas.WeekOut(
        id=week.id,
        number=week.number,
        title=week.title if reveal else f"Неделя {week.number}",
        state=state.value,
        level=level,
        starts_on=week.starts_on,
        ends_on=week.ends_on,
    )
    if not reveal:
        return base
    return base.model_copy(
        update={
            "intro": week.intro,
            "task_min": week.task_min,
            "task_max": week.task_max,
            "word": week.word,
            "word_ru": week.word_ru,
            "word_meaning": week.word_meaning,
        }
    )


def weeks_out(view: PassportView, *, today: date, reveal_all: bool = False) -> list[schemas.WeekOut]:
    result = []
    for week in view.weeks:
        state = view.breakdown.states.get(week.number, WeekState.LOCKED)
        level = view.stamps.get(week.number)
        reveal = reveal_all or week.starts_on <= today
        result.append(week_out(week, state, level.value if level else None, reveal=reveal))
    return result


async def journal_out(
    session: AsyncSession, *, season: SeasonDTO, user_id: int, today: date, principal_admin: bool
) -> schemas.JournalOut:
    view = await passport.build(session, season_id=season.id, user_id=user_id, today=today)
    jview = await journal.build(session, season_id=season.id, user_id=user_id, today=today)
    reasons = await freezes.reasons(session, season.id, user_id)
    reports = await journal.reports_for_user(session, season_id=season.id, user_id=user_id)
    user = await people.get_user(session, user_id)
    assert user is not None
    week_numbers = {w.id: w.number for w in view.weeks}
    return schemas.JournalOut(
        season=season_out(season),
        user=schemas.Me(id=user.id, first_name=user.first_name, username=user.username, is_admin=principal_admin),
        passport=passport_out(view, reasons),
        weeks=weeks_out(view, today=today),
        reports=[reports_out(r, week_ends={w.number: w.ends_on for w in view.weeks}, today=today) for r in reports],
        achievements=jview.achievements,
        words=[
            schemas.WordOut(word=w.word, meaning=w.meaning, week_number=week_numbers.get(w.week_id or -1))
            for w in jview.words
        ],
        season_words=[
            schemas.WordOut(word=w.word, meaning=w.meaning, week_number=w.number) for w in jview.season_words
        ],
        facts=[f.text for f in jview.facts],
        wish=jview.wish,
    )


def reports_out(r: ReportDTO, *, week_ends: dict[int, date], today: date) -> schemas.ReportOut:
    """One report with its files; `editable` while the week is still open (DOMAIN §2)."""
    ends_on = week_ends.get(r.week_number) if r.week_number is not None else None
    return schemas.ReportOut(
        id=r.id,
        week_number=r.week_number,
        kind=r.kind,
        level=r.level,
        text=r.text,
        created_at=r.created_at,
        edited_at=r.edited_at,
        editable=reports.editable_until(ends_on, today),
        media=[
            schemas.MediaOut(id=str(m.media_id), url=f"/media/{m.media_id}", mime=m.mime, downloaded=m.downloaded)
            for m in r.media
        ],
    )


async def media_by_report(session: AsyncSession, report_ids: Sequence[int]) -> dict[int, list[schemas.MediaOut]]:
    """Visible files of the given reports, `{report_id: [MediaOut]}`, in the order they were sent."""
    wanted = sorted({int(report_id) for report_id in report_ids})
    if not wanted:
        return {}
    query = (
        select(models.Media)
        .where(models.Media.report_id.in_(wanted), models.Media.hidden_at.is_(None))
        .order_by(models.Media.created_at, models.Media.id)
    )
    out: dict[int, list[schemas.MediaOut]] = {}
    for m in (await session.execute(query)).scalars():
        out.setdefault(m.report_id, []).append(
            schemas.MediaOut(id=str(m.id), url=f"/media/{m.id}", mime=m.mime, downloaded=m.downloaded_at is not None)
        )
    return out


async def report_out(session: AsyncSession, report_id: int, *, today: date) -> schemas.ReportOut | None:
    """A single report re-read after an edit, in the journal's shape."""
    row = await session.get(models.Report, report_id)
    if row is None:
        return None
    for dto in await journal.reports_for_user(session, season_id=row.season_id, user_id=row.user_id):
        if dto.id == report_id:
            weeks = await content.weeks(session, row.season_id)
            return reports_out(dto, week_ends={w.number: w.ends_on for w in weeks}, today=today)
    return None


def week_word_out(week: WeekDTO) -> schemas.WeekWordOut:
    return schemas.WeekWordOut(
        week_number=week.number, title=week.title, word=week.word, word_ru=week.word_ru, meaning=week.word_meaning
    )


async def home_out(
    session: AsyncSession, *, season: SeasonDTO, user_id: int, today: date, principal_admin: bool, settings: Settings
) -> schemas.HomeOut:
    """The «Сегодня» and «Паспорт» screens: everything the bot's task/today/passport show."""
    view = await passport.build(session, season_id=season.id, user_id=user_id, today=today)
    reasons = await freezes.reasons(session, season.id, user_id)
    user = await people.get_user(session, user_id)
    assert user is not None
    weeks = view.weeks
    current = next((w for w in weeks if w.starts_on <= today <= w.ends_on), None)
    word_week, memory_week = content.daily_words(weeks, current, today)
    tz = tzolkin_day(today) if season.daily_kind == "tzolkin" else None

    week_out_: schemas.CurrentWeekOut | None = None
    if current is not None:
        intents = await people.intents(session, season_id=season.id, week_id=current.id)
        choice = intents.get(user_id)
        level = view.stamps.get(current.number)
        base = week_out(
            current,
            view.breakdown.states.get(current.number, WeekState.CURRENT),
            level.value if level else None,
            reveal=True,
        )
        week_out_ = schemas.CurrentWeekOut(
            **base.model_dump(),
            intent=choice.value if choice else None,
            reports_count=await reports.count_for_week(session, user_id=user_id, week_id=current.id),
            deadline=ru.deadline_short(current),
        )
    upcoming = next((w for w in weeks if w.starts_on > today), None)
    wish = await journal.wish_for(session, season_id=season.id, user_id=user_id)
    return schemas.HomeOut(
        season=season_out(season),
        user=schemas.Me(id=user.id, first_name=user.first_name, username=user.username, is_admin=principal_admin),
        today=schemas.TodayOut(
            date=today,
            tzolkin=None
            if tz is None
            else schemas.TzolkinOut(
                number=tz.number,
                kin=tz.kin,
                sign_name=tz.sign.name,
                sign_symbol=tz.sign.symbol,
                sign_emoji=tz.sign.emoji,
                short=tz.sign.short,
                day_advice=tz.sign.day_advice,
            ),
            word=week_word_out(word_week) if word_week is not None else None,
            memory=week_word_out(memory_week) if memory_week is not None else None,
            note=season.daily_note,
            calendar_url=f"{settings.public_base_url}/calendar" if tz is not None else None,
        ),
        week=week_out_,
        next_week_starts_on=upcoming.starts_on if upcoming is not None else None,
        passport=passport_out(view, reasons),
        weeks=weeks_out(view, today=today),
        achievements=view.achievements,
        wish=wish,
        texts=schemas.TextsOut(
            greeting=ru.greeting(season, app=True),
            help=ru.help_text(app=True),
            end_of_season=ru.end_of_season_text(season),
            write_prompt=ru.WRITE_PROMPT,
            word_prompt=ru.WORD_PROMPT,
            fact_prompt=ru.FACT_PROMPT,
            journal_now=ru.JOURNAL_NOW.format(end=ru.date_genitive(season.ends_on)),
            level_names={(level.value if level else ""): name for level, name in ru.LEVEL_NAMES.items()},
            freeze_reasons=dict(ru.FREEZE_REASONS),
        ),
        links=schemas.LinksOut(
            channel_url=settings.channel_url or None,
            bot_username=settings.bot_username or None,
            admin_app=principal_admin,
        ),
    )


def moscow_today(now: datetime) -> date:
    return to_moscow(now).date()
