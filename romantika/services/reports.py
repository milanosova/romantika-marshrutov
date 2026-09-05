"""Reports: everything a participant sends the bot (DOMAIN §2).

A report is never physically deleted — «это не отчёт» sets `deleted_at` and the stamp of the
week is recomputed from what is left. Media rows are created here but downloaded by
`media.MediaStore`, so accepting a report never waits on the network.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.domain import rules
from romantika.domain.calendar import to_moscow
from romantika.domain.types import ReportKind, StampLevel
from romantika.services import content, freezes, media, people, stamps


@dataclass(frozen=True, slots=True)
class IncomingFile:
    """One attachment of a report, before the bytes are on our disk."""

    kind: ReportKind
    file_id: str | None
    """Telegram's id; None for a file uploaded through the Mini App (stored by `MediaStore.save_upload`)."""
    file_unique_id: str | None = None
    mime: str | None = None
    size: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    """A message the bot decided is a report (not a command, button or dialog answer)."""

    kind: ReportKind
    text: str | None = None
    tg_chat_id: int | None = None
    tg_message_id: int | None = None
    files: list[IncomingFile] = field(default_factory=list)
    client_id: str | None = None
    """Idempotency key of a Mini App submission (see `find_by_client_id`); None from the bot."""


@dataclass(frozen=True, slots=True)
class AcceptResult:
    """What the bot has to tell the participant, and what to hand to the media download."""

    report_id: int
    week_number: int | None
    out_of_week: bool
    level: StampLevel
    stamp_level: StampLevel | None
    freeze_granted: bool
    media_ids: list[uuid.UUID]


#: `FixResult.reason` codes. Named here because the bot matches on them to pick the Russian
#: answer: a literal on either side silently drifts and the participant is told the wrong thing.
NO_WEEK = "no_week"
NO_REPORT = "no_report"
NO_DOWNGRADE = "max_is_not_downgraded"


@dataclass(frozen=True, slots=True)
class FixResult:
    """«Это был максимум/минимум». `reason` is a code; the bot turns it into Russian."""

    ok: bool
    stamp_level: StampLevel | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CancelResult:
    """«Это не отчёт, а сообщение Миле»: the stamp level left after the recomputation."""

    ok: bool
    stamp_level: StampLevel | None
    reason: str | None = None


async def accept(
    session: AsyncSession,
    *,
    season_id: int,
    user_id: int,
    message: IncomingMessage,
    now: datetime,
) -> AcceptResult:
    """Store the report, stamp the week and grant the freeze for the first maximum.

    Outside a week (before the season, between weeks, after the last one) the message is
    still stored — as a letter to Mila with no week and no stamp (DOMAIN §2, §10.2).
    """
    season = await content.require_season(session, season_id)
    await people.ensure_member(session, season_id, user_id, now=now)
    week = await content.current_week(session, season_id, today=to_moscow(now).date())
    # Outside a week the message is a letter, not a report: it is stored as `other` at the
    # minimum level so nothing downstream mistakes it for a week's work (ARCHITECTURE §6).
    kind = message.kind if week is not None else ReportKind.OTHER
    level = rules.report_level(kind)

    report = models.Report(
        season_id=season_id,
        user_id=user_id,
        week_id=None if week is None else week.id,
        kind=kind.value,
        text=message.text,
        level=level.value,
        tg_chat_id=message.tg_chat_id,
        tg_message_id=message.tg_message_id,
        client_id=message.client_id,
        created_at=now,
    )
    session.add(report)
    await session.flush()

    media_rows: list[models.Media] = []
    # Files of one message share `now`; a microsecond per file keeps the order they were sent in
    # (the journal, the editor and the PDF all sort media by `created_at`).
    for position, item in enumerate(message.files):
        row = models.Media(
            report_id=report.id,
            tg_file_id=item.file_id,
            tg_file_unique_id=item.file_unique_id,
            mime=item.mime,
            size=item.size,
            width=item.width,
            height=item.height,
            path=media.new_relative_path(
                season_slug=season.slug,
                user_id=user_id,
                suffix=media.suffix_for(kind=item.kind, mime=item.mime),
            ),
            created_at=now + timedelta(microseconds=position),
        )
        session.add(row)
        media_rows.append(row)
    if media_rows:
        # The uuid primary key is a Python-side default: it exists only after the flush.
        await session.flush()
    media_ids: list[uuid.UUID] = [row.id for row in media_rows]

    if week is None:
        return AcceptResult(
            report_id=report.id,
            week_number=None,
            out_of_week=True,
            level=level,
            stamp_level=None,
            freeze_granted=False,
            media_ids=media_ids,
        )

    stamp = await stamps.merge(
        session,
        season_id=season_id,
        user_id=user_id,
        week_id=week.id,
        week_title=week.title,
        level=level,
        now=now,
    )
    freeze_granted = False
    if stamp.upgraded_to_max:
        freeze_granted = await freezes.grant(
            session,
            season_id=season_id,
            user_id=user_id,
            reason=models.FreezeReason.MAX,
            granted_by=None,
            now=now,
        )
    return AcceptResult(
        report_id=report.id,
        week_number=week.number,
        out_of_week=False,
        level=level,
        stamp_level=stamp.level,
        freeze_granted=freeze_granted,
        media_ids=media_ids,
    )


async def fix_level(
    session: AsyncSession,
    *,
    season_id: int,
    user_id: int,
    week_number: int,
    level: StampLevel,
    now: datetime,
) -> FixResult:
    """The «это был максимум/минимум» buttons: upgrade only, and only with a report."""
    week = await content.week_by_number(session, season_id, week_number)
    if week is None:
        return FixResult(ok=False, stamp_level=None, reason=NO_WEEK)

    current = await stamps.get_level(session, user_id=user_id, week_id=week.id)
    if not await _has_report(session, user_id=user_id, week_id=week.id):
        return FixResult(ok=False, stamp_level=current, reason=NO_REPORT)
    if current is StampLevel.MAX and level is StampLevel.MIN:
        return FixResult(ok=False, stamp_level=StampLevel.MAX, reason=NO_DOWNGRADE)

    stamp = await stamps.merge(
        session,
        season_id=season_id,
        user_id=user_id,
        week_id=week.id,
        week_title=week.title,
        level=level,
        now=now,
    )
    if stamp.upgraded_to_max:
        await freezes.grant(
            session,
            season_id=season_id,
            user_id=user_id,
            reason=models.FreezeReason.MAX,
            granted_by=None,
            now=now,
        )
    return FixResult(ok=True, stamp_level=stamp.level, reason=None)


async def cancel(session: AsyncSession, *, user_id: int, report_id: int, now: datetime) -> CancelResult:
    """«Это не отчёт, а сообщение Миле»: mark it deleted and recompute the week's stamp.

    The row itself stays forever; a stamp Mila set by hand is not touched (DOMAIN §2).
    """
    report = await session.get(models.Report, report_id)
    if report is None or report.user_id != user_id:
        return CancelResult(ok=False, stamp_level=None, reason="not_yours")
    if report.deleted_at is not None:
        return CancelResult(ok=False, stamp_level=None, reason="already_cancelled")

    report.deleted_at = now
    await session.flush()
    if report.week_id is None:
        return CancelResult(ok=True, stamp_level=None)
    recomputed = await _recompute_stamp(
        session, season_id=report.season_id, user_id=user_id, week_id=report.week_id, now=now
    )
    return CancelResult(ok=True, stamp_level=recomputed.level)


@dataclass(frozen=True, slots=True)
class Recomputed:
    """The week's stamp after the live reports were re-read: its level and whether a first
    maximum appeared (which earns the automatic freeze, DOMAIN §3)."""

    level: StampLevel | None
    upgraded_to_max: bool


async def live_counts(session: AsyncSession, *, season_id: int, week_id: int) -> dict[int, int]:
    """`{user_id: number of live reports}` on one week, for the admin's people list."""
    query = (
        select(models.Report.user_id, func.count())
        .where(
            models.Report.season_id == season_id,
            models.Report.week_id == week_id,
            models.Report.deleted_at.is_(None),
        )
        .group_by(models.Report.user_id)
    )
    return {int(user_id): int(count) for user_id, count in (await session.execute(query)).all()}


async def _recompute_stamp(
    session: AsyncSession, *, season_id: int, user_id: int, week_id: int, now: datetime
) -> Recomputed:
    """Set the stamp of a week to the best level among its live reports.

    A stamp Mila set by hand (`source = admin`) is left alone. Without live reports the
    automatic stamp is removed; a report edited up to a photo raises it to the maximum.
    """
    query = select(models.Stamp).where(models.Stamp.user_id == user_id, models.Stamp.week_id == week_id)
    stamp = (await session.execute(query)).scalar_one_or_none()
    if stamp is not None and stamp.source == models.StampSource.ADMIN.value:
        return Recomputed(level=StampLevel(stamp.level), upgraded_to_max=False)

    levels = await _remaining_levels(session, user_id=user_id, week_id=week_id)
    # Mila took the stamp away after seeing these reports: neither editing nor cancelling
    # anything brings them back into the count, even once a later report has earned the week
    # again. Only reports sent after her decision speak for the week.
    cleared = await stamps.cleared_reports(session, user_id=user_id, week_id=week_id)
    levels = {rid: level for rid, level in levels.items() if rid not in cleared}
    if not levels:
        if stamp is not None:
            await session.delete(stamp)
            await session.flush()
        return Recomputed(level=None, upgraded_to_max=False)

    level = StampLevel.MAX if StampLevel.MAX in levels.values() else StampLevel.MIN
    if stamp is None:
        week = await session.get(models.Week, week_id)
        merged = await stamps.merge(
            session,
            season_id=season_id,
            user_id=user_id,
            week_id=week_id,
            week_title=week.title if week else "",
            level=level,
            now=now,
        )
        return Recomputed(level=merged.level, upgraded_to_max=merged.upgraded_to_max)
    previous = StampLevel(stamp.level)
    if previous is not level:
        stamp.level = level.value
        stamp.awarded_at = now
        await session.flush()
    return Recomputed(level=level, upgraded_to_max=level is StampLevel.MAX and previous is not StampLevel.MAX)


# --- editing (Mini App) ------------------------------------------------------------------

NOT_YOURS = "not_yours"
CANCELLED = "cancelled"
WEEK_OVER = "week_over"
EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class EditResult:
    """What an edit did: the report's new level, the week's stamp after it, new media rows
    to fill with bytes, and whether the edit earned the first-maximum freeze."""

    ok: bool
    reason: str | None = None
    report_id: int | None = None
    week_number: int | None = None
    level: StampLevel | None = None
    stamp_level: StampLevel | None = None
    freeze_granted: bool = False
    media_ids: list[uuid.UUID] = field(default_factory=list)
    text_changed: bool = False
    removed: int = 0


def editable_until(week_ends_on: date | None, today: date) -> bool:
    """A report may be edited while its week is open (DOMAIN §2); letters and past weeks are read-only."""
    return week_ends_on is not None and today <= week_ends_on


async def edit(
    session: AsyncSession,
    *,
    user_id: int,
    report_id: int,
    text: str | None,
    new_files: list[IncomingFile],
    remove_media_ids: list[uuid.UUID],
    now: datetime,
    edit_key: str | None = None,
) -> EditResult:
    """Change the text and the files of one's own report while its week is open.

    Removed files are hidden, never deleted (DOMAIN §2); the previous text is kept in the
    audit log. The report's level follows what is left (a photo is a maximum, text alone a
    minimum) and the week's stamp is recomputed from all live reports — the same recomputation
    «это не отчёт» does, so editing a photo away lowers a stamp only if nothing else holds it.
    """
    report = await session.get(models.Report, report_id)
    if report is None or report.user_id != user_id:
        return EditResult(ok=False, reason=NOT_YOURS)
    if report.deleted_at is not None:
        return EditResult(ok=False, reason=CANCELLED)
    week = await session.get(models.Week, report.week_id) if report.week_id is not None else None
    if week is None or not editable_until(week.ends_on, to_moscow(now).date()):
        return EditResult(ok=False, reason=WEEK_OVER)

    season = await content.require_season(session, report.season_id)
    body = (text or "").strip() or None
    text_changed = body != (report.text or None)

    media_query = (
        select(models.Media)
        .where(models.Media.report_id == report.id, models.Media.hidden_at.is_(None))
        .order_by(models.Media.created_at, models.Media.id)
    )
    live_media = list((await session.execute(media_query)).scalars())
    to_remove = set(remove_media_ids)
    kept = [row for row in live_media if row.id not in to_remove]
    if not body and not kept and not new_files:
        # Nothing would be left — refuse before anything is written.
        return EditResult(ok=False, reason=EMPTY)

    content.audit(
        session,
        actor_id=user_id,
        action="edit",
        entity="report",
        entity_id=str(report.id),
        before={"text": report.text},
        after={
            "text": body,
            "edit_key": edit_key,
            "added": len(new_files),
            "removed": len(live_media) - len(kept),
        },
    )
    report.text = body
    removed = 0
    for row in live_media:
        if row.id in to_remove:
            row.hidden_at = now
            removed += 1

    # New files go after everything the report already has, whatever the clock says (a retry or
    # a frozen test clock can hand two edits the same `now`).
    latest = max((row.created_at for row in live_media), default=now)
    first_at = now if now > latest else latest + timedelta(microseconds=1)
    new_rows: list[models.Media] = []
    for position, item in enumerate(new_files):
        row = models.Media(
            report_id=report.id,
            tg_file_id=item.file_id,
            tg_file_unique_id=item.file_unique_id,
            mime=item.mime,
            size=item.size,
            width=item.width,
            height=item.height,
            path=media.new_relative_path(
                season_slug=season.slug,
                user_id=user_id,
                suffix=media.suffix_for(kind=item.kind, mime=item.mime),
            ),
            created_at=first_at + timedelta(microseconds=position),
        )
        session.add(row)
        new_rows.append(row)
    if new_rows:
        await session.flush()

    kinds = [media.kind_for_mime(row.mime) for row in kept] + [item.kind for item in new_files]
    kind = kinds[0] if kinds else ReportKind.TEXT
    level = StampLevel.MAX if any(rules.report_level(k) is StampLevel.MAX for k in kinds) else StampLevel.MIN
    report.kind = kind.value
    report.level = level.value
    report.edited_at = now
    await session.flush()

    recomputed = await _recompute_stamp(session, season_id=report.season_id, user_id=user_id, week_id=week.id, now=now)
    freeze_granted = False
    if recomputed.upgraded_to_max:
        freeze_granted = await freezes.grant(
            session,
            season_id=report.season_id,
            user_id=user_id,
            reason=models.FreezeReason.MAX,
            granted_by=None,
            now=now,
        )
    return EditResult(
        ok=True,
        report_id=report.id,
        week_number=week.number,
        level=level,
        stamp_level=recomputed.level,
        freeze_granted=freeze_granted,
        media_ids=[row.id for row in new_rows],
        text_changed=text_changed,
        removed=removed,
    )


async def edit_applied(session: AsyncSession, *, report_id: int, edit_key: str) -> bool:
    """Whether an edit with this key was already applied to the report (a retried PATCH)."""
    query = select(models.AuditLog.id).where(
        models.AuditLog.entity == "report",
        models.AuditLog.entity_id == str(report_id),
        models.AuditLog.action == "edit",
        models.AuditLog.after["edit_key"].astext == edit_key,
    )
    return (await session.execute(query)).first() is not None


async def find_by_client_id(session: AsyncSession, *, user_id: int, client_id: str) -> models.Report | None:
    """The report a Mini App submission already created, when the client retries it."""
    query = select(models.Report).where(models.Report.user_id == user_id, models.Report.client_id == client_id)
    return (await session.execute(query)).scalar_one_or_none()


async def count_for_week(session: AsyncSession, *, user_id: int, week_id: int) -> int:
    """Live (not cancelled) reports of one participant for one week."""
    query = select(func.count(models.Report.id)).where(
        models.Report.user_id == user_id,
        models.Report.week_id == week_id,
        models.Report.deleted_at.is_(None),
    )
    return int((await session.execute(query)).scalar_one())


async def _has_report(session: AsyncSession, *, user_id: int, week_id: int) -> bool:
    query = select(models.Report.id).where(
        models.Report.user_id == user_id,
        models.Report.week_id == week_id,
        models.Report.deleted_at.is_(None),
    )
    return (await session.execute(query.limit(1))).first() is not None


async def _remaining_levels(session: AsyncSession, *, user_id: int, week_id: int) -> dict[int, StampLevel]:
    """`{report_id: level}` of the live reports of one week."""
    query = select(models.Report.id, models.Report.level).where(
        models.Report.user_id == user_id,
        models.Report.week_id == week_id,
        models.Report.deleted_at.is_(None),
    )
    return {rid: StampLevel(level) for rid, level in (await session.execute(query)).tuples().all()}
