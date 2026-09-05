"""Unit tests for the tzolkin day count and its data file."""

from __future__ import annotations

import json
from datetime import date, timedelta

from romantika.domain.tzolkin import CORRELATION, SIGNS, TONES, TZOLKIN_PATH, tzolkin_day


def test_data_file_has_no_empty_fields() -> None:
    for sign in SIGNS:
        for field_name in ("name", "name_academic", "latin", "emoji", "symbol", "meaning", "destiny", "short"):
            assert getattr(sign, field_name), f"{sign.name}.{field_name} is empty"
    assert [tone.number for tone in TONES] == list(range(1, 14))
    assert all(tone.name and tone.text for tone in TONES)


def test_names_are_unique_and_academic_spelling_is_kept() -> None:
    assert len({sign.name for sign in SIGNS}) == 20
    academic = {sign.name: sign.name_academic for sign in SIGNS}
    assert academic["Ик"] == "Ик'"
    assert academic["Эцнаб"] == "Эц'наб"
    assert all("'" not in sign.name for sign in SIGNS)


def test_imix_keeps_both_traditional_symbols() -> None:
    """DOMAIN §10: the bot said «водяная лилия», the Mini App said «Крокодил»."""
    assert SIGNS[0].symbol == "Крокодил · водяная лилия"


def test_correlation_matches_the_data_file() -> None:
    raw = json.loads(TZOLKIN_PATH.read_text(encoding="utf-8"))
    assert raw["correlation"] == CORRELATION == 584283


def test_every_kin_appears_once_per_cycle() -> None:
    start = date(2026, 1, 1)
    days = [tzolkin_day(start + timedelta(days=offset)) for offset in range(260)]
    assert len({day.kin for day in days}) == 260
    assert {(day.number, day.sign.name) for day in days} == {(day.number, day.sign.name) for day in days}
    assert len({(day.number, day.sign.name) for day in days}) == 260


def test_consecutive_days_advance_tone_and_sign() -> None:
    today = tzolkin_day(date(2026, 9, 3))
    tomorrow = tzolkin_day(date(2026, 9, 4))
    assert tomorrow.number == today.number % 13 + 1
    assert SIGNS.index(tomorrow.sign) == (SIGNS.index(today.sign) + 1) % 20
    assert tomorrow.kin == today.kin % 260 + 1
