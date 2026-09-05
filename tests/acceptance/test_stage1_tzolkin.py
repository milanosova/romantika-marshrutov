"""Stage 1 acceptance: tzolkin calendar (ARCHITECTURE §5, data/tzolkin.json).

Expected values were produced by the legacy implementation (legacy/бот/бот.py:419-458)
and match the Mini App (legacy tzolkin/index.html). READ-ONLY for implementers.

`Sign.name` uses the simple spelling without apostrophes («Ик», «Акбаль», «Кан», «Маник»,
«Эцнаб»); the academic spelling with apostrophes may be kept in an extra field.
"""

from __future__ import annotations

from datetime import date

import pytest

from romantika.domain.tzolkin import SIGNS, TONES, julian_day, tzolkin_day

SIGN_NAMES = [
    "Имиш", "Ик", "Акбаль", "Кан", "Чикчан", "Кими", "Маник", "Ламат", "Мулук", "Ок",
    "Чуэн", "Эб", "Бен", "Иш", "Мен", "Киб", "Кабан", "Эцнаб", "Кавак", "Ахау",
]  # fmt: skip


def test_sign_catalog_matches_legacy_order() -> None:
    assert [s.name for s in SIGNS] == SIGN_NAMES
    assert len(TONES) == 13
    assert all(sign.day_advice for sign in SIGNS), "every sign has a daily phrase (legacy 3rd field)"
    assert all(sign.destiny for sign in SIGNS), "every sign has a destiny text (Mini App `dest`)"


@pytest.mark.parametrize(
    ("day", "jdn"),
    [
        (date(2026, 8, 31), 2461284),
        (date(2026, 9, 3), 2461287),
        (date(2000, 1, 1), 2451545),
        (date(2012, 12, 21), 2456283),
    ],
)
def test_julian_day(day: date, jdn: int) -> None:
    assert julian_day(day) == jdn


@pytest.mark.parametrize(
    ("day", "number", "sign_name"),
    [
        (date(2026, 8, 31), 13, "Имиш"),
        (date(2026, 9, 3), 3, "Кан"),
        (date(2026, 11, 18), 1, "Ахау"),
        (date(2012, 12, 21), 4, "Ахау"),
        (date(1990, 5, 17), 5, "Кими"),
        (date(2000, 1, 1), 11, "Ик"),
    ],
)
def test_tzolkin_day_matches_legacy(day: date, number: int, sign_name: str) -> None:
    result = tzolkin_day(day)
    assert result.number == number
    assert result.sign.name == sign_name
    assert 1 <= result.kin <= 260
    sign_index = SIGN_NAMES.index(sign_name)
    assert (result.kin - 1) % 13 == number - 1
    assert (result.kin - 1) % 20 == sign_index


def test_cycle_is_260_days() -> None:
    a = tzolkin_day(date(2026, 1, 1))
    b = tzolkin_day(date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + 260))
    assert (a.number, a.sign.name, a.kin) == (b.number, b.sign.name, b.kin)
