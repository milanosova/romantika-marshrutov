"""Unit tests for the pure season rules (docs/DOMAIN.md §2-§5)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from romantika.domain.rules import (
    core_members,
    level_for,
    merge_level,
    report_level,
    season_breakdown,
    total_freezes,
)
from romantika.domain.types import Breakdown, Level, LevelConfig, ReportKind, StampLevel, WeekInfo, WeekState

FIRST_MONDAY = date(2026, 8, 31)


def weeks(count: int = 12) -> list[WeekInfo]:
    return [
        WeekInfo(
            number=index + 1,
            title=f"Week {index + 1}",
            starts_on=FIRST_MONDAY + timedelta(days=7 * index),
            ends_on=FIRST_MONDAY + timedelta(days=7 * index + 6),
        )
        for index in range(count)
    ]


def run(
    stamps: dict[int, StampLevel],
    today: date,
    *,
    bonus: int = 0,
    joined_on: date = date(2026, 8, 20),
    base: int = 2,
    cap: int = 5,
) -> Breakdown:
    return season_breakdown(
        weeks=weeks(),
        stamps=stamps,
        bonus_freezes=bonus,
        base_freezes=base,
        max_freezes=cap,
        joined_on=joined_on,
        today=today,
    )


def test_every_report_kind_has_a_level() -> None:
    for kind in ReportKind:
        assert report_level(kind) in (StampLevel.MIN, StampLevel.MAX)
    assert report_level(ReportKind.OTHER) is StampLevel.MIN


def test_total_freezes_is_capped_and_never_negative() -> None:
    assert total_freezes(bonus_freezes=0, base_freezes=2, max_freezes=5) == 2
    assert total_freezes(bonus_freezes=9, base_freezes=2, max_freezes=5) == 5
    assert total_freezes(bonus_freezes=-3, base_freezes=2, max_freezes=5) == 2


def test_merge_level_is_commutative_on_max() -> None:
    assert merge_level(None, StampLevel.MAX) is StampLevel.MAX
    assert merge_level(StampLevel.MIN, StampLevel.MIN) is StampLevel.MIN


def test_before_the_season_everything_is_locked() -> None:
    breakdown = run({}, date(2026, 8, 20))
    assert set(breakdown.states.values()) == {WeekState.LOCKED}
    assert (breakdown.stamps, breakdown.freezes_used, breakdown.best_streak) == (0, 0, 0)


def test_a_late_freeze_repairs_a_past_miss() -> None:
    """Deliberately kept from legacy: spending is recomputed on every view."""
    stamps = {1: StampLevel.MAX, 5: StampLevel.MIN}
    without_bonus = run(stamps, date(2026, 10, 8))
    assert without_bonus.states[4] is WeekState.MISSED
    assert (without_bonus.best_streak, without_bonus.current_streak) == (1, 1)

    with_bonus = run(stamps, date(2026, 10, 8), bonus=1)
    assert with_bonus.states[4] is WeekState.FROZEN
    assert (with_bonus.best_streak, with_bonus.current_streak) == (2, 2)


def test_a_week_that_started_before_joining_never_spends_a_freeze() -> None:
    breakdown = run({}, date(2026, 9, 23), joined_on=date(2026, 9, 21))
    assert breakdown.states[1] is WeekState.BEFORE_JOIN
    assert breakdown.states[4] is WeekState.CURRENT
    assert breakdown.freezes_used == 0


def test_current_week_is_never_a_miss() -> None:
    breakdown = run({}, date(2026, 9, 2))
    assert breakdown.states[1] is WeekState.CURRENT
    assert breakdown.freezes_used == 0
    assert breakdown.current_streak == 0


def test_weeks_are_walked_in_number_order() -> None:
    shuffled = list(reversed(weeks()))
    breakdown = season_breakdown(
        weeks=shuffled,
        stamps={1: StampLevel.MIN, 3: StampLevel.MIN},
        bonus_freezes=0,
        base_freezes=2,
        max_freezes=5,
        joined_on=date(2026, 8, 20),
        today=date(2026, 9, 30),
    )
    assert list(breakdown.states) == list(range(1, 13))
    assert breakdown.best_streak == 2


@pytest.mark.parametrize(
    ("stamps_count", "freezes_left", "expected"),
    [(0, 2, None), (1, 0, Level.TOURIST), (3, 0, Level.TOURIST), (8, 5, Level.TRAVELER), (9, 1, Level.RESIDENT)],
)
def test_level_for(stamps_count: int, freezes_left: int, expected: Level | None) -> None:
    assert level_for(stamps_count, freezes_left, LevelConfig()) is expected


def test_core_is_empty_without_streaks() -> None:
    lonely = run({2: StampLevel.MIN}, date(2026, 9, 23))
    assert core_members({7: lonely}) == []
    assert core_members({}) == []


def test_stamps_keyed_by_anything_but_week_numbers_are_a_data_error() -> None:
    # `stamps.week_id` and `week.number` are both ints: without this the passport of a
    # participant with four stamps would quietly render as "nothing done yet".
    with pytest.raises(ValueError, match="week number"):
        run({101: StampLevel.MAX, 102: StampLevel.MAX}, date(2026, 11, 25))


def test_a_stamp_on_a_week_that_has_not_started_still_counts() -> None:
    breakdown = run({1: StampLevel.MIN, 12: StampLevel.MAX}, date(2026, 9, 16))
    assert breakdown.states[12] is WeekState.LOCKED
    assert breakdown.stamps == 2
    assert breakdown.best_streak == 1
