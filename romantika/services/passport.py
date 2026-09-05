"""The passport view model (DOMAIN §3, §4, §7).

All the arithmetic lives in `domain.rules`; this module only gathers the rows it needs —
stamps by week number, earned freezes, the day the participant joined the season — and
hands the result to the bot, the Mini App and the PDF unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from romantika.domain import rules
from romantika.domain.calendar import to_moscow
from romantika.domain.types import Breakdown, Level, StampLevel
from romantika.services import achievements, content, freezes, people, stamps
from romantika.services.content import SeasonDTO, WeekDTO


@dataclass(frozen=True, slots=True)
class PassportView:
    """Everything one participant's passport shows."""

    user_id: int
    season: SeasonDTO
    weeks: list[WeekDTO]
    breakdown: Breakdown
    stamps: dict[int, StampLevel]
    """`{week_number: level}` — the stars in the week list."""
    stamp_titles: dict[int, str]
    """`{week_number: title}` frozen at award time (DOMAIN §1)."""
    stamps_max: int
    weeks_total: int
    level: Level | None
    achievements: list[str]
    joined_on: date


async def build(session: AsyncSession, *, season_id: int, user_id: int, today: date) -> PassportView:
    """Walk the season for one participant and collect what the passport screen needs."""
    season = await content.require_season(session, season_id)
    weeks = await content.weeks(session, season_id)
    levels = await stamps.for_user(session, season_id=season_id, user_id=user_id)
    joined_on = await joined_day(session, season=season, user_id=user_id)

    breakdown = rules.season_breakdown(
        weeks=[week.info for week in weeks],
        stamps=levels,
        bonus_freezes=await freezes.bonus_count(session, season_id, user_id),
        base_freezes=season.base_freezes,
        max_freezes=season.max_freezes,
        joined_on=joined_on,
        today=today,
    )
    return PassportView(
        user_id=user_id,
        season=season,
        weeks=weeks,
        breakdown=breakdown,
        stamps=levels,
        stamp_titles=await stamps.titles_for_user(session, season_id=season_id, user_id=user_id),
        stamps_max=sum(1 for level in levels.values() if level is StampLevel.MAX),
        weeks_total=len(weeks),
        level=rules.level_for(breakdown.stamps, breakdown.freezes_left, season.levels),
        achievements=await achievements.labels(session, season_id=season_id, user_id=user_id),
        joined_on=joined_on,
    )


async def joined_day(session: AsyncSession, *, season: SeasonDTO, user_id: int) -> date:
    """The Moscow day the participant joined the season.

    Missing membership is an error, not a default: guessing the season's first day would
    silently turn every week before the real first contact into a missed one and burn the
    freezes of a person who was not there yet (DOMAIN §3). Every service that records
    activity (`reports.accept`, `words.add`, `people.set_intent`, `stamps.admin_set`) joins
    the person to the season, so a missing row means the caller invented the participant.
    """
    joined = await people.member_joined_at(session, season.id, user_id)
    if joined is None:
        raise people.MembershipMissingError(f"user {user_id} is not a member of season {season.id}")
    return to_moscow(joined).date()
