"""Unit tests for the business calendar."""

from __future__ import annotations

from datetime import date, datetime

from romantika.domain.calendar import (
    MOSCOW,
    julian_day,
    moscow_now,
    moscow_today,
    released_weeks,
    to_moscow,
    week_for,
)
from romantika.domain.types import WeekInfo

WEEKS = [
    WeekInfo(number=1, title="one", starts_on=date(2026, 8, 31), ends_on=date(2026, 9, 6)),
    WeekInfo(number=2, title="two", starts_on=date(2026, 9, 7), ends_on=date(2026, 9, 13)),
]


def test_moscow_now_is_aware() -> None:
    now = moscow_now()
    assert now.tzinfo is not None
    assert moscow_today() == now.date()


def test_to_moscow_reads_naive_datetimes_as_moscow() -> None:
    naive = datetime(2026, 8, 31, 12, 0)
    assert to_moscow(naive).utcoffset() == MOSCOW.utcoffset(naive)
    utc_noon = datetime(2026, 8, 31, 12, 0, tzinfo=MOSCOW).astimezone()
    assert to_moscow(utc_noon).hour == 12


def test_week_for_boundaries() -> None:
    assert week_for(date(2026, 8, 30), WEEKS) is None
    assert week_for(date(2026, 8, 31), WEEKS) == WEEKS[0]
    assert week_for(date(2026, 9, 6), WEEKS) == WEEKS[0]
    assert week_for(date(2026, 9, 7), WEEKS) == WEEKS[1]
    assert week_for(date(2026, 9, 14), WEEKS) is None


def test_released_weeks_hides_the_future() -> None:
    assert released_weeks(date(2026, 9, 1), WEEKS) == [WEEKS[0]]
    assert released_weeks(date(2026, 8, 1), WEEKS) == []
    assert released_weeks(date(2026, 9, 7), WEEKS) == WEEKS


def test_julian_day_is_continuous() -> None:
    assert julian_day(date(2026, 9, 4)) - julian_day(date(2026, 9, 3)) == 1
    assert julian_day(date(1970, 1, 1)) == 2440588
