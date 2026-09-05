"""Admin API: season content, participants, stamps, freezes, achievements, wishes, facts."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from romantika.db import models
from romantika.domain import rules
from romantika.domain.types import StampLevel, WeekState
from romantika.services import (
    achievements,
    content,
    facts,
    freezes,
    journal,
    letters,
    notify,
    passport,
    people,
    reminders,
    reports,
    stamps,
    summary,
    wishes,
)
from romantika.services.content import WeekDTO
from romantika.texts import ru
from romantika.web import schemas, views
from romantika.web.deps import AdminDep, NowDep, SeasonDep, SessionDep, TodayDep

router = APIRouter(prefix="/api/admin", tags=["admin"])


# --- content ------------------------------------------------------------------------


@router.get("/weeks", response_model=list[schemas.AdminWeekOut])
async def list_weeks(
    _: AdminDep, session: SessionDep, season: SeasonDep, today: TodayDep
) -> list[schemas.AdminWeekOut]:
    return [_admin_week(week, today) for week in await content.weeks(session, season.id)]


def _admin_week(week: WeekDTO, today: date) -> schemas.AdminWeekOut:
    """Mila's calendar view of a week: locked (future), current, or over (`stamped` here means past)."""
    if week.starts_on > today:
        state = WeekState.LOCKED
    elif week.ends_on >= today:
        state = WeekState.CURRENT
    else:
        state = WeekState.STAMPED
    return schemas.AdminWeekOut(**views.week_out(week, state, None, reveal=True).model_dump())


@router.put("/weeks/{week_id}", response_model=schemas.AdminWeekOut)
async def edit_week(
    week_id: int, body: schemas.WeekEdit, admin: AdminDep, session: SessionDep, today: TodayDep
) -> schemas.AdminWeekOut:
    changes = {key: value for key, value in body.model_dump().items() if value is not None}
    if not changes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "nothing to change")
    try:
        week = await content.update_week(session, actor_id=admin.user.id, week_id=week_id, changes=changes, today=today)
    except content.ContentError as exc:
        code = status.HTTP_404_NOT_FOUND if "does not exist" in str(exc) else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return _admin_week(week, today)


@router.get("/achievement-types", response_model=list[schemas.AchievementTypeOut])
async def achievement_types(_: AdminDep, session: SessionDep, season: SeasonDep) -> list[schemas.AchievementTypeOut]:
    return [
        schemas.AchievementTypeOut(code=a.code, emoji=a.emoji, name=a.name, description=a.description, label=a.label)
        for a in await achievements.catalogue(session, season.id)
    ]


# --- participants -----------------------------------------------------------------


@router.get("/participants", response_model=list[schemas.ParticipantOut])
async def participants(
    _: AdminDep, session: SessionDep, season: SeasonDep, today: TodayDep
) -> list[schemas.ParticipantOut]:
    breakdowns = await summary.breakdowns_for_season(session, season=season, today=today)
    all_stamps = await stamps.for_season(session, season.id)
    users = {u.id: u for u in await people.all_users(session)}
    current = await content.current_week(session, season.id, today=today)
    intents = await people.intents(session, season_id=season.id, week_id=current.id) if current else {}
    report_counts = await reports.live_counts(session, season_id=season.id, week_id=current.id) if current else {}
    result = []
    for user_id, breakdown in breakdowns.items():
        user = users.get(user_id)
        if user is None:
            continue
        levels = all_stamps.get(user_id, {})
        week_level = levels.get(current.number) if current else None
        intent = intents.get(user_id)
        result.append(
            schemas.ParticipantOut(
                id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                username=user.username,
                joined_at=user.joined_at,
                stamps=breakdown.stamps,
                stamps_max=sum(1 for level in levels.values() if level is StampLevel.MAX),
                level=(lvl := rules.level_for(breakdown.stamps, breakdown.freezes_left, season.levels)) and lvl.value,
                freezes_left=breakdown.freezes_left,
                freezes_total=breakdown.freezes_total,
                best_streak=breakdown.best_streak,
                current_streak=breakdown.current_streak,
                week_intent=intent.value if intent else None,
                week_level=week_level.value if week_level else None,
                week_reports=report_counts.get(user_id, 0),
            )
        )
    result.sort(key=lambda p: (p.joined_at, p.id))
    return result


async def _require_member(session: SessionDep, season: SeasonDep, user_id: int, now: NowDep) -> None:
    if await people.get_user(session, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such participant")
    await people.ensure_member(session, season.id, user_id, now=now)


@router.get("/participants/{user_id}", response_model=schemas.ParticipantDetail)
async def participant(
    user_id: int, _: AdminDep, session: SessionDep, season: SeasonDep, today: TodayDep, now: NowDep
) -> schemas.ParticipantDetail:
    await _require_member(session, season, user_id, now)
    view = await passport.build(session, season_id=season.id, user_id=user_id, today=today)
    reasons = await freezes.reasons(session, season.id, user_id)
    jview = await journal.build(session, season_id=season.id, user_id=user_id, today=today)
    reports = await journal.reports_for_user(session, season_id=season.id, user_id=user_id)
    user = await people.get_user(session, user_id)
    assert user is not None
    return schemas.ParticipantDetail(
        user=schemas.ParticipantOut(
            id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            joined_at=user.joined_at,
            stamps=view.breakdown.stamps,
            stamps_max=view.stamps_max,
            level=view.level.value if view.level else None,
            freezes_left=view.breakdown.freezes_left,
            freezes_total=view.breakdown.freezes_total,
            best_streak=view.breakdown.best_streak,
            current_streak=view.breakdown.current_streak,
        ),
        passport=views.passport_out(view, reasons),
        weeks=views.weeks_out(view, today=today, reveal_all=True),
        achievements=view.achievements,
        wish=jview.wish,
        reports=[
            views.reports_out(r, week_ends={w.number: w.ends_on for w in view.weeks}, today=today) for r in reports
        ],
        words=[schemas.WordOut(word=w.word, meaning=w.meaning) for w in jview.words],
    )


@router.put("/participants/{user_id}/stamps/{week_number}", response_model=schemas.StampOut)
async def set_stamp(
    user_id: int,
    week_number: int,
    body: schemas.StampSet,
    admin: AdminDep,
    session: SessionDep,
    season: SeasonDep,
    now: NowDep,
) -> schemas.StampOut:
    await _require_member(session, season, user_id, now)
    try:
        level = await stamps.admin_set(
            session,
            actor_id=admin.user.id,
            season_id=season.id,
            user_id=user_id,
            week_number=week_number,
            level=StampLevel(body.level) if body.level else None,
            now=now,
        )
    except content.ContentError as exc:
        code = status.HTTP_409_CONFLICT if "not started" in str(exc) else status.HTTP_404_NOT_FOUND
        raise HTTPException(code, str(exc)) from exc
    return schemas.StampOut(level=level.value if level else None)


@router.post("/participants/{user_id}/freezes", response_model=schemas.FreezeOut, status_code=status.HTTP_201_CREATED)
async def grant_freeze(
    user_id: int,
    body: schemas.FreezeGrant,
    admin: AdminDep,
    session: SessionDep,
    season: SeasonDep,
    now: NowDep,
    response: Response,
) -> schemas.FreezeOut:
    await _require_member(session, season, user_id, now)
    granted = await freezes.grant(
        session,
        season_id=season.id,
        user_id=user_id,
        reason=models.FreezeReason(body.reason),
        granted_by=admin.user.id,
        now=now,
        note=body.note,
    )
    if not granted:
        response.status_code = status.HTTP_200_OK  # the cap: nothing new was created
    elif user_id != admin.user.id:
        await notify.enqueue_message(session, chat_id=user_id, text=ru.freeze_given(body.reason), now=now)
    return schemas.FreezeOut(granted=granted, freezes_total=await freezes.total(session, season.id, user_id))


@router.post(
    "/participants/{user_id}/achievements", response_model=schemas.AchievementOut, status_code=status.HTTP_201_CREATED
)
async def grant_achievement(
    user_id: int,
    body: schemas.AchievementGrant,
    admin: AdminDep,
    session: SessionDep,
    season: SeasonDep,
    now: NowDep,
    response: Response,
) -> schemas.AchievementOut:
    await _require_member(session, season, user_id, now)
    result = await achievements.award(
        session, season_id=season.id, user_id=user_id, code_or_text=body.code_or_text, awarded_by=admin.user.id, now=now
    )
    if result.created and user_id != admin.user.id:
        await notify.enqueue_message(session, chat_id=user_id, text=ru.achievement_given(result.label), now=now)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return schemas.AchievementOut(code=result.code, label=result.label, created=result.created)


@router.put("/participants/{user_id}/wish", response_model=schemas.WishSet)
async def set_wish(
    user_id: int, body: schemas.WishSet, _: AdminDep, session: SessionDep, season: SeasonDep, now: NowDep
) -> schemas.WishSet:
    await _require_member(session, season, user_id, now)
    await wishes.set_wish(session, season_id=season.id, user_id=user_id, text=body.text, now=now)
    return body


# --- summary, facts, audit ----------------------------------------------------------


@router.get("/summary", response_model=schemas.SummaryOut)
async def week_summary(
    _: AdminDep, session: SessionDep, season: SeasonDep, today: TodayDep, week: int | None = None
) -> schemas.SummaryOut:
    if week is None:
        current = await content.current_week(session, season.id, today=today)
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no current week; pass ?week=N")
        week = current.number
    try:
        report = await summary.week(session, season_id=season.id, week_number=week, today=today)
        draft = await summary.draft_post(session, season_id=season.id, week_number=week, today=today)
    except content.ContentError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    names = await people.display_names(session, report.took + list(report.submitted))
    week_row = await content.week_by_number(session, season.id, week)
    return schemas.SummaryOut(
        week_ended=week_row is not None and week_row.ends_on < today,
        week_number=report.week_number,
        week_title=report.week_title,
        members_total=report.members_total,
        reports_total=report.reports_total,
        took=report.took,
        took_names=[names.get(u, str(u)) for u in report.took],
        submitted=[
            schemas.SubmittedOut(user_id=u, name=names.get(u, str(u)), level=lvl.value)
            for u, lvl in report.submitted.items()
        ],
        took_not_submitted=report.took_not_submitted,
        took_not_submitted_names=[names.get(u, str(u)) for u in report.took_not_submitted],
        core_best=report.core_best,
        core_current=report.core_current,
        draft_post=draft.text,
        draft_notes=draft.notes,
    )


@router.get("/facts", response_model=list[schemas.FactOut])
async def list_facts(_: AdminDep, session: SessionDep, season: SeasonDep) -> list[schemas.FactOut]:
    listed = await facts.list_active(session, season.id)
    names = await people.display_names(session, [f.author_id for f in listed if f.author_id is not None], short=True)
    return [
        schemas.FactOut(
            id=f.id,
            text=f.text,
            author_id=f.author_id,
            author_name=names.get(f.author_id) if f.author_id is not None else None,
            week_id=f.week_id,
            created_at=f.created_at,
        )
        for f in listed
    ]


@router.post("/facts", response_model=schemas.FactOut, status_code=status.HTTP_201_CREATED)
async def add_fact(
    body: schemas.FactCreate, _: AdminDep, session: SessionDep, season: SeasonDep, now: NowDep, today: TodayDep
) -> schemas.FactOut:
    week_id: int | None = None
    if body.week_number is not None:
        week = await content.week_by_number(session, season.id, body.week_number)
        if week is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"недели {body.week_number} в этом сезоне нет")
        week_id = week.id
    else:
        current = await content.current_week(session, season.id, today=today)
        week_id = current.id if current else None
    fact_id = await facts.add(session, season_id=season.id, week_id=week_id, text=body.text, author_id=None, now=now)
    return schemas.FactOut(
        id=fact_id, text=body.text, author_id=None, author_name=None, week_id=week_id, created_at=now
    )


@router.delete("/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fact(fact_id: int, admin: AdminDep, session: SessionDep, now: NowDep) -> Response:
    removed = await facts.remove(session, fact_id=fact_id, actor_id=admin.user.id, now=now)
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such fact")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audit", response_model=list[schemas.AuditOut])
async def audit(_: AdminDep, session: SessionDep, limit: int = 100) -> list[schemas.AuditOut]:
    query = select(models.AuditLog).order_by(models.AuditLog.id.desc()).limit(min(max(limit, 1), 500))
    rows = list((await session.execute(query)).scalars())
    names = await people.display_names(session, [row.actor_id for row in rows if row.actor_id], short=True)
    return [
        schemas.AuditOut(
            id=row.id,
            actor_id=row.actor_id,
            actor_name=names.get(row.actor_id) if row.actor_id else None,
            action=row.action,
            entity=row.entity,
            entity_id=row.entity_id,
            before=row.before,
            after=row.after,
            created_at=row.created_at,
        )
        for row in rows
    ]


# --- what the bot panel also has: reminders, a message to a participant -----------


@router.get("/reminders", response_model=schemas.RemindersOut)
async def reminders_state(_: AdminDep, session: SessionDep) -> schemas.RemindersOut:
    return schemas.RemindersOut(enabled=await reminders.enabled(session))


@router.put("/reminders", response_model=schemas.RemindersOut)
async def reminders_toggle(body: schemas.RemindersIn, _: AdminDep, session: SessionDep) -> schemas.RemindersOut:
    """The Thursday/Sunday auto-reminders switch (DOMAIN §8); same setting as `/reminders`."""
    await reminders.set_enabled(session, body.enabled)
    return schemas.RemindersOut(enabled=body.enabled)


@router.post("/remind", response_model=schemas.QueuedOut, status_code=status.HTTP_202_ACCEPTED)
async def remind_now(
    admin: AdminDep, session: SessionDep, season: SeasonDep, now: NowDep, body: schemas.RemindIn | None = None
) -> schemas.QueuedOut:
    """«Напомнить сейчас»: the worker sends the texts and tells Mila how many went out.

    The week comes from the screen Mila pressed the button on; a past week is refused, because
    a reminder about it would ask for a report nobody can hand in any more (DOMAIN §2).
    """
    week_number = body.week_number if body else None
    if week_number is not None:
        week = await content.week_by_number(session, season.id, week_number)
        if week is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such week")
        if week.ends_on < views.moscow_today(now):
            raise HTTPException(status.HTTP_409_CONFLICT, "неделя уже прошла — напоминать не о чем")
        if week.starts_on > views.moscow_today(now):
            raise HTTPException(status.HTTP_409_CONFLICT, "неделя ещё не началась — задание пока никто не видел")
    job_id = await notify.enqueue_reminders_now(
        session, season_id=season.id, requested_by=admin.user.id, now=now, week_number=week_number
    )
    return schemas.QueuedOut(job_id=job_id)


# --- letters: Mila's inbox ---------------------------------------------------------


@router.get("/letters", response_model=schemas.LettersOut)
async def list_letters(_: AdminDep, session: SessionDep, season: SeasonDep) -> schemas.LettersOut:
    listed = await letters.list_for_season(session, season.id)
    names = await people.display_names(session, [item.user_id for item in listed])
    media = await views.media_by_report(session, [item.report_id for item in listed if item.report_id])
    return schemas.LettersOut(
        unanswered=await letters.unanswered_count(session, season.id),
        letters=[
            schemas.LetterOut(
                id=item.id,
                user_id=item.user_id,
                author=names.get(item.user_id, str(item.user_id)),
                source=item.source.value,
                text=item.text,
                created_at=item.created_at,
                reply_text=item.reply_text,
                replied_at=item.replied_at,
                report_id=item.report_id,
                media=media.get(item.report_id or -1, []),
            )
            for item in listed
        ],
    )


@router.post("/letters/{letter_id}/reply", response_model=schemas.QueuedOut, status_code=status.HTTP_202_ACCEPTED)
async def reply_to_letter(
    letter_id: int, body: schemas.TextIn, admin: AdminDep, session: SessionDep, now: NowDep
) -> schemas.QueuedOut:
    """Answer a letter from the inbox: delivered by the bot as «Мила ответила…», marked answered."""
    letter = await letters.get(session, letter_id)
    if letter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such letter")
    text = body.text.strip()
    await letters.mark_replied(session, letter_id, reply_text=text, replied_by=admin.user.id, now=now)
    job_id = await notify.enqueue_message(
        session, chat_id=letter.user_id, text=ru.reply_to_author(text, about="letter"), now=now
    )
    return schemas.QueuedOut(job_id=job_id)


@router.post("/participants/{user_id}/message", response_model=schemas.QueuedOut, status_code=status.HTTP_202_ACCEPTED)
async def message_participant(
    user_id: int, body: schemas.TextIn, _: AdminDep, session: SessionDep, season: SeasonDep, now: NowDep
) -> schemas.QueuedOut:
    """A reply to a participant from the admin app — delivered as «Мила ответила…» in the bot."""
    await _require_member(session, season, user_id, now)
    job_id = await notify.enqueue_message(
        session, chat_id=user_id, text=ru.reply_to_author(body.text.strip(), about="message"), now=now
    )
    return schemas.QueuedOut(job_id=job_id)
