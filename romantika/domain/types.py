"""Value types shared by the pure domain functions (docs/DOMAIN.md §2-§5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class ReportKind(StrEnum):
    """What a participant sent us; drives the stamp level."""

    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    VIDEO_NOTE = "video_note"
    DOCUMENT = "document"
    VOICE = "voice"
    AUDIO = "audio"
    OTHER = "other"


class StampLevel(StrEnum):
    MIN = "min"
    MAX = "max"


class WeekState(StrEnum):
    """How one season week looks in a participant's passport."""

    LOCKED = "locked"
    STAMPED = "stamped"
    CURRENT = "current"
    BEFORE_JOIN = "before_join"
    FROZEN = "frozen"
    MISSED = "missed"


class Level(StrEnum):
    TOURIST = "tourist"
    TRAVELER = "traveler"
    RESIDENT = "resident"


@dataclass(frozen=True, slots=True)
class LevelConfig:
    """Stamp thresholds of a season."""

    tourist: int = 1
    traveler: int = 4
    resident: int = 9


@dataclass(frozen=True, slots=True)
class WeekInfo:
    """Calendar facts about a week; content lives in the DB row."""

    number: int
    title: str
    starts_on: date
    ends_on: date

    def contains(self, day: date) -> bool:
        return self.starts_on <= day <= self.ends_on


@dataclass(frozen=True, slots=True)
class Breakdown:
    """Result of walking a season week by week for one participant."""

    states: dict[int, WeekState] = field(default_factory=dict)
    stamps: int = 0
    freezes_used: int = 0
    freezes_left: int = 0
    freezes_total: int = 0
    best_streak: int = 0
    current_streak: int = 0


@dataclass(frozen=True, slots=True)
class Sign:
    """One of the 20 tzolkin day signs (data/tzolkin.json)."""

    name: str
    name_academic: str
    latin: str
    emoji: str
    symbol: str
    meaning: str
    destiny: str
    short: str
    day_advice: str


@dataclass(frozen=True, slots=True)
class Tone:
    """One of the 13 tzolkin tones."""

    number: int
    name: str
    text: str


@dataclass(frozen=True, slots=True)
class TzolkinDay:
    number: int
    sign: Sign
    kin: int
