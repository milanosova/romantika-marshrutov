"""Stamps: one per participant and week, never downgraded by a later report (DOMAIN §2).

The week title is copied into the stamp at award time, so reordering or renaming a week
later does not rewrite anybody's passport.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.domain import rules
from romantika.domain.calendar import to_moscow
from romantika.domain.types import StampLevel
from romantika.services import content, people


@dataclass(frozen=True, slots=True)
class StampResult:
    """What `merge` did: the level now, the level before, and whether the row is new."""

    level: StampLevel
    previous: StampLevel | None
    created: bool

    @property
    def upgraded_to_max(self) -> bool:
        """The moment that earns the automatic freeze (DOMAIN §3)."""
        return self.level is StampLevel.MAX and self.previous is not StampLevel.MAX


async def _row(session: AsyncSession, *, user_id: int, week_id: int, for_update: bool = False) -> models.Stamp | None:
    query = select(models.Stamp).where(models.Stamp.user_id == user_id, models.Stamp.week_id == week_id)
    if for_update:
        # Two updates of the same album arrive concurrently; the loser waits here instead of
        # blowing up on the unique constraint and rolling back its own report.
        query = query.with_for_update()
    return (await session.execute(query)).scalar_one_or_none()


async def get_level(session: AsyncSession, *, user_id: int, week_id: int) -> StampLevel | None:
    row = await _row(session, user_id=user_id, week_id=week_id)
    return None if row is None else StampLevel(row.level)


async def merge(
    session: AsyncSession,
    *,
    season_id: int,
    user_id: int,
    week_id: int,
    week_title: str,
    level: StampLevel,
    now: datetime,
) -> StampResult:
    """Award the stamp or raise its level; a maximum is never lowered back to a minimum.

    A report that actually changes the level takes the provenance with it: the row stops
    claiming `source = admin`, so `reports.cancel` may recompute it from the reports that
    are left (DOMAIN §2, §10.9). Overriding a stamp Mila set by hand is audited, so her
    grant stays visible even after the recomputation.
    """
    row = await _row(session, user_id=user_id, week_id=week_id, for_update=True)
    if row is None:
        insert = (
            pg_insert(models.Stamp)
            .values(
                season_id=season_id,
                user_id=user_id,
                week_id=week_id,
                level=level.value,
                week_title_snapshot=week_title,
                awarded_at=now,
                source=models.StampSource.REPORT.value,
            )
            .on_conflict_do_nothing(constraint="uq_stamps_user_id_week_id")
            .returning(models.Stamp.id)
        )
        if (await session.execute(insert)).scalar_one_or_none() is not None:
            return StampResult(level=level, previous=None, created=True)
        # Another worker inserted the row first; its transaction is committed by now.
        row = await _row(session, user_id=user_id, week_id=week_id, for_update=True)
        if row is None:  # pragma: no cover - the conflicting row cannot vanish again
            raise RuntimeError(f"stamp of user {user_id} for week {week_id} disappeared")

    previous = StampLevel(row.level)
    merged = rules.merge_level(previous, level)
    if merged is not previous:
        if row.source == models.StampSource.ADMIN.value:
            content.audit(
                session,
                actor_id=user_id,
                action="override",
                entity="stamp",
                entity_id=f"{user_id}:{week_id}",
                before={"level": previous.value, "source": row.source},
                after={"level": merged.value, "source": models.StampSource.REPORT.value},
            )
        row.level = merged.value
        row.source = models.StampSource.REPORT.value
        row.awarded_at = now
        await session.flush()
    return StampResult(level=merged, previous=previous, created=False)


async def admin_set(
    session: AsyncSession,
    *,
    actor_id: int | None,
    season_id: int,
    user_id: int,
    week_number: int,
    level: StampLevel | None,
    now: datetime,
) -> StampLevel | None:
    """Mila's override for any week, including a downgrade and a removal; audited."""
    week = await content.week_by_number(session, season_id, week_number)
    if week is None:
        raise content.ContentError(f"season {season_id} has no week {week_number}")
    if level is not None and week.starts_on > to_moscow(now).date():
        # The passport walk (rules.season_breakdown) only counts weeks that have started; a stamp
        # here would show as «🔒 закрыта» and «⭐» at once.
        raise content.ContentError(f"week {week_number} has not started yet")
    if level is not None:
        # A stamp implies participation: without the membership row every season-wide view
        # would count this person in the numerator and not in the denominator.
        await people.ensure_member(session, season_id, user_id, now=now)

    row = await _row(session, user_id=user_id, week_id=week.id)
    before = None if row is None else {"level": row.level, "source": row.source}

    cleared: list[int] = []
    if level is None:
        if row is None:
            return None
        cleared = await _live_report_ids(session, user_id=user_id, week_id=week.id)
        await session.delete(row)
    elif row is None:
        session.add(
            models.Stamp(
                season_id=season_id,
                user_id=user_id,
                week_id=week.id,
                level=level.value,
                week_title_snapshot=week.title,
                awarded_at=now,
                source=models.StampSource.ADMIN.value,
            )
        )
    else:
        row.level = level.value
        row.source = models.StampSource.ADMIN.value
        row.awarded_at = now

    content.audit(
        session,
        actor_id=actor_id,
        action="set" if level is not None else "clear",
        entity="stamp",
        entity_id=f"{user_id}:{week.id}",
        before=before,
        after={"cleared_reports": cleared}
        if level is None
        else {"level": level.value, "source": models.StampSource.ADMIN.value},
    )
    await session.flush()
    return level


async def cleared_reports(session: AsyncSession, *, user_id: int, week_id: int) -> set[int]:
    """Ids of the reports Mila had in front of her when she last removed this week's stamp.

    Editing or cancelling one of them must not bring the stamp back (that would undo her
    decision), while a report sent later earns the week as usual.
    """
    query = (
        select(models.AuditLog.after)
        .where(
            models.AuditLog.entity == "stamp",
            models.AuditLog.entity_id == f"{user_id}:{week_id}",
            models.AuditLog.action == "clear",
        )
        .order_by(models.AuditLog.id.desc())
        .limit(1)
    )
    after = (await session.execute(query)).scalar_one_or_none()
    return {int(rid) for rid in (after or {}).get("cleared_reports", [])}


async def _live_report_ids(session: AsyncSession, *, user_id: int, week_id: int) -> list[int]:
    query = select(models.Report.id).where(
        models.Report.user_id == user_id, models.Report.week_id == week_id, models.Report.deleted_at.is_(None)
    )
    return [int(rid) for rid in (await session.execute(query)).scalars()]


async def level_for_week(session: AsyncSession, *, user_id: int, week_id: int | None) -> StampLevel | None:
    """The stamp one participant has for one week, if any."""
    if week_id is None:
        return None
    row = await _row(session, user_id=user_id, week_id=week_id)
    return None if row is None else StampLevel(row.level)


async def for_user(session: AsyncSession, *, season_id: int, user_id: int) -> dict[int, StampLevel]:
    """`{week_number: level}` — keyed by number, the way `rules.season_breakdown` wants it."""
    query = (
        select(models.Week.number, models.Stamp.level)
        .join(models.Week, models.Week.id == models.Stamp.week_id)
        .where(models.Stamp.season_id == season_id, models.Stamp.user_id == user_id)
    )
    return {number: StampLevel(level) for number, level in (await session.execute(query)).all()}


async def for_season(session: AsyncSession, season_id: int) -> dict[int, dict[int, StampLevel]]:
    """`{user_id: {week_number: level}}` for the whole season, in one query."""
    query = (
        select(models.Stamp.user_id, models.Week.number, models.Stamp.level)
        .join(models.Week, models.Week.id == models.Stamp.week_id)
        .where(models.Stamp.season_id == season_id)
    )
    result: dict[int, dict[int, StampLevel]] = {}
    for user_id, number, level in (await session.execute(query)).all():
        result.setdefault(user_id, {})[number] = StampLevel(level)
    return result


async def for_week(session: AsyncSession, *, season_id: int, week_id: int) -> dict[int, StampLevel]:
    """`{user_id: level}` of one week, ordered by the moment the stamp was awarded."""
    query = (
        select(models.Stamp.user_id, models.Stamp.level)
        .where(models.Stamp.season_id == season_id, models.Stamp.week_id == week_id)
        .order_by(models.Stamp.awarded_at, models.Stamp.id)
    )
    return {user_id: StampLevel(level) for user_id, level in (await session.execute(query)).all()}


async def titles_for_user(session: AsyncSession, *, season_id: int, user_id: int) -> dict[int, str]:
    """`{week_number: week_title_snapshot}` — what the passport shows for stamped weeks."""
    query = (
        select(models.Week.number, models.Stamp.week_title_snapshot)
        .join(models.Week, models.Week.id == models.Stamp.week_id)
        .where(models.Stamp.season_id == season_id, models.Stamp.user_id == user_id)
    )
    return {int(number): str(title) for number, title in (await session.execute(query)).all()}


async def users_with_stamps(session: AsyncSession, season_id: int) -> set[int]:
    """Everyone with at least one stamp this season — the journal recipients at its end."""
    query = select(models.Stamp.user_id).where(models.Stamp.season_id == season_id).distinct()
    return {int(user_id) for user_id in (await session.execute(query)).scalars()}
