"""Achievements: granted by hand only, «за поступок, а не за посещаемость» (DOMAIN §6).

An achievement is either a code from the season catalogue — then its label is «emoji name» —
or a free line Mila typed, which is its own code and its own label. The same achievement is
never granted twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.services.errors import Refused

#: `achievements.code` is a `varchar(64)`; a longer free text is stored trimmed.
CODE_LENGTH = 64

#: `achievements.label` is a `varchar(255)`; a longer free text is trimmed the same way.
LABEL_LENGTH = 255


@dataclass(frozen=True, slots=True)
class AchievementTypeDTO:
    """One entry of the season catalogue (`data/seasons/*.json` → `achievement_types`)."""

    code: str
    emoji: str
    name: str
    description: str
    label: str


@dataclass(frozen=True, slots=True)
class AwardResult:
    created: bool
    code: str
    label: str


def catalogue_label(row: models.AchievementType) -> str:
    return f"{row.emoji} {row.name}".strip()


async def award(
    session: AsyncSession,
    *,
    season_id: int,
    user_id: int,
    code_or_text: str,
    awarded_by: int | None,
    now: datetime,
) -> AwardResult:
    """Grant an achievement by catalogue code or by free text; repeats do nothing."""
    text = code_or_text.strip()
    if not text:
        raise Refused("нужен код ачивки или её текст")

    known = (
        await session.execute(
            select(models.AchievementType).where(
                models.AchievementType.season_id == season_id, models.AchievementType.code == text
            )
        )
    ).scalar_one_or_none()
    if known is not None:
        code, label = known.code, catalogue_label(known)
    else:
        # Both columns are bounded: an over-long free text would abort the whole
        # transaction in Postgres and lose everything else the admin did in it.
        code, label = text[:CODE_LENGTH], text[:LABEL_LENGTH]

    existing = (
        await session.execute(
            select(models.Achievement).where(
                models.Achievement.season_id == season_id,
                models.Achievement.user_id == user_id,
                models.Achievement.code == code,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return AwardResult(created=False, code=code, label=existing.label)

    session.add(
        models.Achievement(
            season_id=season_id,
            user_id=user_id,
            code=code,
            label=label,
            awarded_by=awarded_by,
            awarded_at=now,
        )
    )
    await session.flush()
    return AwardResult(created=True, code=code, label=label)


async def labels(session: AsyncSession, *, season_id: int, user_id: int) -> list[str]:
    """Labels of one participant, in the order they were granted (passport and journal)."""
    query = (
        select(models.Achievement.label)
        .where(models.Achievement.season_id == season_id, models.Achievement.user_id == user_id)
        .order_by(models.Achievement.awarded_at, models.Achievement.id)
    )
    return list((await session.execute(query)).scalars())


async def catalogue(session: AsyncSession, season_id: int) -> list[AchievementTypeDTO]:
    """The season catalogue, for the admin keyboard. Ordered the way the season file lists it."""
    query = (
        select(models.AchievementType)
        .where(models.AchievementType.season_id == season_id)
        .order_by(models.AchievementType.sort, models.AchievementType.id)
    )
    return [
        AchievementTypeDTO(
            code=row.code,
            emoji=row.emoji,
            name=row.name,
            description=row.description,
            label=catalogue_label(row),
        )
        for row in (await session.execute(query)).scalars()
    ]
