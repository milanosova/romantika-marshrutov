"""Business calendar: Moscow time, season weeks, julian day numbers. Pure, no IO."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from romantika.domain.types import WeekInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def moscow_now() -> datetime:
    """Timezone-aware «now» in the club's calendar. Never use `datetime.now()` bare."""
    return datetime.now(MOSCOW)


def moscow_today() -> date:
    return moscow_now().date()


def to_moscow(moment: datetime) -> datetime:
    """Interpret a naive datetime as Moscow time, convert an aware one."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=MOSCOW)
    return moment.astimezone(MOSCOW)


def week_for(day: date, weeks: Sequence[WeekInfo]) -> WeekInfo | None:
    """The week that contains `day`, or None between weeks and outside the season."""
    for week in weeks:
        if week.contains(day):
            return week
    return None


def released_weeks(day: date, weeks: Sequence[WeekInfo]) -> list[WeekInfo]:
    """Weeks already announced by `day`; future weeks are never shown."""
    return [week for week in weeks if week.starts_on <= day]


def julian_day(day: date) -> int:
    """Julian day number (Fliegel-Van Flandern), same formula as the legacy bot."""
    a = (14 - day.month) // 12
    y = day.year + 4800 - a
    m = day.month + 12 * a - 3
    return day.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
