"""Tzolkin day count (Mexico season «Сегодня»), GMT correlation 584283.

`data/tzolkin.json` is the single source of truth for signs and tones: the bot and the
Mini App used to disagree about them (DOMAIN §10).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Final

from romantika.config import DATA_DIR
from romantika.domain.calendar import julian_day
from romantika.domain.types import Sign, Tone, TzolkinDay

__all__ = ["CORRELATION", "SIGNS", "TONES", "julian_day", "tzolkin_day"]

TZOLKIN_PATH: Final[Path] = DATA_DIR / "tzolkin.json"


def _load(path: Path) -> tuple[int, tuple[Sign, ...], tuple[Tone, ...]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    signs = tuple(
        Sign(
            name=item["name"],
            name_academic=item["name_academic"],
            latin=item["latin"],
            emoji=item["emoji"],
            symbol=item["symbol"],
            meaning=item["meaning"],
            destiny=item["destiny"],
            short=item["short"],
            day_advice=item["day_advice"],
        )
        for item in raw["signs"]
    )
    tones = tuple(Tone(number=item["number"], name=item["name"], text=item["text"]) for item in raw["tones"])
    if len(signs) != 20 or len(tones) != 13:
        raise ValueError(f"{path} must hold 20 signs and 13 tones, got {len(signs)}/{len(tones)}")
    return int(raw["correlation"]), signs, tones


CORRELATION, SIGNS, TONES = _load(TZOLKIN_PATH)


def tzolkin_day(day: date) -> TzolkinDay:
    """Tone (1..13), sign and kin (1..260) of a calendar day — legacy formulas verbatim."""
    x = julian_day(day) - CORRELATION
    number = ((x + 3) % 13) + 1
    sign_index = (x + 19) % 20
    kin = (x + 159) % 260 + 1
    return TzolkinDay(number=number, sign=SIGNS[sign_index], kin=kin)
