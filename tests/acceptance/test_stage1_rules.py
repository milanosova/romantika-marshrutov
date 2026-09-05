"""Stage 1 acceptance: pure domain rules (docs/DOMAIN.md §2–§5, ARCHITECTURE §5).

READ-ONLY for implementers. If a test looks wrong, report it — do not edit.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from romantika.domain.rules import (
    core_members,
    level_for,
    merge_level,
    report_level,
    season_breakdown,
)
from romantika.domain.types import (
    Breakdown,
    Level,
    LevelConfig,
    ReportKind,
    StampLevel,
    WeekInfo,
    WeekState,
)

FIRST_MONDAY = date(2026, 8, 31)


def make_weeks(n: int = 12) -> list[WeekInfo]:
    weeks: list[WeekInfo] = []
    for i in range(n):
        start = FIRST_MONDAY + timedelta(days=7 * i)
        weeks.append(
            WeekInfo(number=i + 1, title=f"Неделя {i + 1}", starts_on=start, ends_on=start + timedelta(days=6))
        )
    return weeks


def breakdown(
    *,
    stamps: dict[int, StampLevel],
    today: date,
    joined_on: date = date(2026, 8, 20),
    bonus: int = 0,
    base: int = 2,
    cap: int = 5,
) -> Breakdown:
    return season_breakdown(
        weeks=make_weeks(),
        stamps=stamps,
        bonus_freezes=bonus,
        base_freezes=base,
        max_freezes=cap,
        joined_on=joined_on,
        today=today,
    )


# --- report level ---------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [ReportKind.PHOTO, ReportKind.VIDEO, ReportKind.VIDEO_NOTE, ReportKind.DOCUMENT],
)
def test_media_reports_are_max(kind: ReportKind) -> None:
    assert report_level(kind) is StampLevel.MAX


@pytest.mark.parametrize("kind", [ReportKind.TEXT, ReportKind.VOICE, ReportKind.AUDIO])
def test_text_like_reports_are_min(kind: ReportKind) -> None:
    assert report_level(kind) is StampLevel.MIN


def test_max_never_downgrades() -> None:
    assert merge_level(None, StampLevel.MIN) is StampLevel.MIN
    assert merge_level(StampLevel.MIN, StampLevel.MAX) is StampLevel.MAX
    assert merge_level(StampLevel.MAX, StampLevel.MIN) is StampLevel.MAX
    assert merge_level(StampLevel.MAX, StampLevel.MAX) is StampLevel.MAX


# --- season breakdown ----------------------------------------------------------


def test_two_stamps_current_week_three() -> None:
    b = breakdown(stamps={1: StampLevel.MAX, 2: StampLevel.MIN}, today=date(2026, 9, 16))
    assert b.states[1] is WeekState.STAMPED
    assert b.states[2] is WeekState.STAMPED
    assert b.states[3] is WeekState.CURRENT
    assert all(b.states[n] is WeekState.LOCKED for n in range(4, 13))
    assert (b.stamps, b.freezes_used, b.freezes_left, b.freezes_total) == (2, 0, 2, 2)
    assert (b.best_streak, b.current_streak) == (2, 2)


def test_freezes_keep_the_streak() -> None:
    b = breakdown(stamps={1: StampLevel.MAX, 3: StampLevel.MIN}, today=date(2026, 9, 30))
    assert b.states[2] is WeekState.FROZEN
    assert b.states[3] is WeekState.STAMPED
    assert b.states[4] is WeekState.FROZEN
    assert b.states[5] is WeekState.CURRENT
    assert (b.stamps, b.freezes_used, b.freezes_left, b.freezes_total) == (2, 2, 0, 2)
    assert (b.best_streak, b.current_streak) == (2, 2)


def test_missed_week_after_freezes_run_out_breaks_the_streak() -> None:
    b = breakdown(stamps={1: StampLevel.MAX}, today=date(2026, 9, 30))
    assert b.states[2] is WeekState.FROZEN
    assert b.states[3] is WeekState.FROZEN
    assert b.states[4] is WeekState.MISSED
    assert b.states[5] is WeekState.CURRENT
    assert (b.stamps, b.freezes_used, b.freezes_left) == (1, 2, 0)
    assert (b.best_streak, b.current_streak) == (1, 0)


def test_weeks_before_joining_cost_nothing() -> None:
    b = breakdown(stamps={3: StampLevel.MIN}, today=date(2026, 9, 23), joined_on=date(2026, 9, 10))
    assert b.states[1] is WeekState.BEFORE_JOIN
    assert b.states[2] is WeekState.BEFORE_JOIN
    assert b.states[3] is WeekState.STAMPED
    assert b.states[4] is WeekState.CURRENT
    assert (b.freezes_used, b.freezes_left) == (0, 2)
    assert (b.best_streak, b.current_streak) == (1, 1)


def test_bonus_freezes_are_capped() -> None:
    b = breakdown(stamps={}, today=date(2026, 8, 25), bonus=4)
    assert b.freezes_total == 5
    b = breakdown(stamps={}, today=date(2026, 8, 25), bonus=1)
    assert b.freezes_total == 3


def test_after_season_end_all_weeks_are_resolved() -> None:
    stamps = {n: StampLevel.MIN for n in range(1, 10)}
    b = breakdown(stamps=stamps, today=date(2026, 11, 25))
    assert b.states[10] is WeekState.FROZEN
    assert b.states[11] is WeekState.FROZEN
    assert b.states[12] is WeekState.MISSED
    assert WeekState.CURRENT not in b.states.values()
    assert (b.stamps, b.freezes_used, b.freezes_left) == (9, 2, 0)
    assert (b.best_streak, b.current_streak) == (9, 0)


def test_current_week_with_stamp_is_stamped() -> None:
    b = breakdown(stamps={1: StampLevel.MIN}, today=date(2026, 9, 2))
    assert b.states[1] is WeekState.STAMPED
    assert b.current_streak == 1


# --- levels and core -----------------------------------------------------------


def test_levels_by_stamps() -> None:
    cfg = LevelConfig(tourist=1, traveler=4, resident=9)
    assert level_for(0, 2, cfg) is None
    assert level_for(1, 2, cfg) is Level.TOURIST
    assert level_for(3, 2, cfg) is Level.TOURIST
    assert level_for(4, 2, cfg) is Level.TRAVELER
    assert level_for(9, 2, cfg) is Level.RESIDENT
    assert level_for(12, 1, cfg) is Level.RESIDENT


def test_resident_needs_freezes_left() -> None:
    cfg = LevelConfig(tourist=1, traveler=4, resident=9)
    assert level_for(9, 0, cfg) is Level.TRAVELER
    assert level_for(4, 0, cfg) is Level.TRAVELER


def test_core_members_by_best_streak() -> None:
    a = breakdown(stamps={1: StampLevel.MAX, 2: StampLevel.MIN, 3: StampLevel.MIN}, today=date(2026, 9, 23))
    b = breakdown(stamps={1: StampLevel.MAX, 2: StampLevel.MIN}, today=date(2026, 9, 23))
    c = breakdown(stamps={2: StampLevel.MIN}, today=date(2026, 9, 23))
    d = breakdown(stamps={}, today=date(2026, 9, 23))
    assert core_members({10: a, 20: b, 30: c, 40: d}) == [10, 20]
    assert core_members({10: a, 20: b, 30: c, 40: d}, min_streak=3) == [10]
    assert core_members({20: b, 10: a}) == [10, 20]
