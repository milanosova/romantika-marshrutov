"""Season rules: report levels, the week-by-week breakdown, levels and the core.

Pure functions, ported from the legacy bot (`разбор_сезона`, `всего_заморозок`, `УРОВНИ`,
`ядро`) and fixed by docs/DOMAIN.md §2-§5.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from romantika.domain.types import (
    Breakdown,
    Level,
    LevelConfig,
    ReportKind,
    StampLevel,
    WeekInfo,
    WeekState,
)

MAX_KINDS: frozenset[ReportKind] = frozenset(
    {ReportKind.PHOTO, ReportKind.VIDEO, ReportKind.VIDEO_NOTE, ReportKind.DOCUMENT}
)


def report_level(kind: ReportKind) -> StampLevel:
    """Photo, video, video note and document are a maximum; everything else a minimum."""
    return StampLevel.MAX if kind in MAX_KINDS else StampLevel.MIN


def merge_level(existing: StampLevel | None, new: StampLevel) -> StampLevel:
    """A maximum is never downgraded — not by a later text, not by a button."""
    if existing is StampLevel.MAX or new is StampLevel.MAX:
        return StampLevel.MAX
    return StampLevel.MIN


def total_freezes(*, bonus_freezes: int, base_freezes: int, max_freezes: int) -> int:
    """Base freezes of the season plus earned ones, capped by the season ceiling."""
    return min(base_freezes + max(bonus_freezes, 0), max_freezes)


def season_breakdown(
    *,
    weeks: Sequence[WeekInfo],
    stamps: Mapping[int, StampLevel],
    bonus_freezes: int,
    base_freezes: int,
    max_freezes: int,
    joined_on: date,
    today: date,
) -> Breakdown:
    """Walk the season from the first week to the last, spending freezes on the way.

    Freeze spending is recomputed on every view (never stored), so a freeze granted later
    turns a past miss back into a frozen week — deliberately kept from legacy.

    `stamps` is keyed by **week number**, not by `stamps.week_id`: both are `int`, so a
    caller that passes ids would silently get an honest-looking "nothing done" passport.
    Keys that match no week are therefore a data error, not zero stamps.
    """
    numbers = {week.number for week in weeks}
    unknown = sorted(set(stamps) - numbers)
    if unknown:
        raise ValueError(
            f"stamps must be keyed by week number, not week id: {unknown} match no week of "
            f"this season (numbers {sorted(numbers)})"
        )

    total = total_freezes(bonus_freezes=bonus_freezes, base_freezes=base_freezes, max_freezes=max_freezes)
    states: dict[int, WeekState] = {}
    used = 0
    # Every stamp counts, as in legacy: a stamp Mila put on a week that has not started yet
    # (DOMAIN §2) leaves that week `locked` for everyone, but the participant has it.
    stamped = len(stamps)
    streak = 0
    best = 0

    for week in sorted(weeks, key=lambda w: w.number):
        if today < week.starts_on:
            states[week.number] = WeekState.LOCKED
            continue
        if week.number in stamps:
            states[week.number] = WeekState.STAMPED
            streak += 1
            best = max(best, streak)
            continue
        if week.contains(today):
            states[week.number] = WeekState.CURRENT
            continue
        if week.starts_on < joined_on:
            states[week.number] = WeekState.BEFORE_JOIN
            continue
        if used < total:
            states[week.number] = WeekState.FROZEN
            used += 1
            continue
        states[week.number] = WeekState.MISSED
        streak = 0

    return Breakdown(
        states=states,
        stamps=stamped,
        freezes_used=used,
        freezes_left=total - used,
        freezes_total=total,
        best_streak=best,
        current_streak=streak,
    )


def level_for(stamps_count: int, freezes_left: int, cfg: LevelConfig) -> Level | None:
    """Level by stamps; residency also requires unspent freezes (DOMAIN §4)."""
    if stamps_count >= cfg.resident and freezes_left > 0:
        return Level.RESIDENT
    if stamps_count >= cfg.traveler:
        return Level.TRAVELER
    if stamps_count >= cfg.tourist:
        return Level.TOURIST
    return None


def core_members(breakdowns: Mapping[int, Breakdown], min_streak: int = 2) -> list[int]:
    """Participants with a streak of at least `min_streak` weeks, best streak first."""
    members = [
        (user_id, breakdown)
        for user_id, breakdown in breakdowns.items()
        if breakdown.stamps >= 1 and breakdown.best_streak >= min_streak
    ]
    members.sort(key=lambda item: (-item[1].best_streak, item[0]))
    return [user_id for user_id, _ in members]
