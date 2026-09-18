"""Users, season membership, dialog state and weekly intents (DOMAIN §1, §7, §10.8).

Like every service this module flushes but never commits, takes time explicitly and returns
dataclasses — an ORM object handed to a handler would lazy-load after the session is gone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.services import locks
from romantika.services.content import WeekDTO
from romantika.services.errors import Refused

#: A «ждём от человека...» state older than this is stale and ignored (DOMAIN §10.8).
DIALOG_TTL = timedelta(hours=6)


class MembershipMissingError(LookupError):
    """Asked for a season view of somebody who never joined that season."""


@dataclass(frozen=True, slots=True)
class TelegramUser:
    """What Telegram tells us about the sender of an update."""

    id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


@dataclass(frozen=True, slots=True)
class UserDTO:
    """A person as the rest of the app sees them."""

    id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    joined_at: datetime
    is_admin: bool = False

    @property
    def display_name(self) -> str:
        """Name for texts and admin lists; falls back to the username, then to the id."""
        parts = [part for part in (self.first_name, self.last_name) if part]
        if parts:
            return " ".join(parts)
        return f"@{self.username}" if self.username else str(self.id)

    @property
    def display_name_with_username(self) -> str:
        """«Имя (@ник)» — how the admin sees people in lists and headers."""
        if self.username and self.first_name:
            return f"{self.display_name} (@{self.username})"
        return self.display_name


@dataclass(frozen=True, slots=True)
class DialogStateDTO:
    user_id: int
    state: str
    payload: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None


def to_dto(row: models.User) -> UserDTO:
    """Map the ORM row to the DTO; the only place that knows both."""
    return UserDTO(
        id=row.id,
        username=row.username,
        first_name=row.first_name,
        last_name=row.last_name,
        joined_at=row.joined_at,
        is_admin=row.is_admin,
    )


async def upsert_user(session: AsyncSession, tg: TelegramUser, *, now: datetime) -> UserDTO:
    """Create the person on the first contact, refresh the name later; `joined_at` is kept."""
    row = await session.get(models.User, tg.id)
    if row is None:
        # The first two requests of a newcomer arrive together (two tabs, the app's parallel
        # fetches): an idempotent insert lets the second one find the first one's row instead
        # of hitting the primary key. No advisory lock here — it would be held for the whole
        # update, media downloads included, and queue every request of the person behind it.
        await session.execute(
            pg_insert(models.User).values(id=tg.id, joined_at=now).on_conflict_do_nothing(index_elements=["id"])
        )
        row = await session.get(models.User, tg.id, populate_existing=True)
        if row is None:  # pragma: no cover - the insert or the concurrent one has committed by now
            raise RuntimeError(f"user {tg.id} vanished after an idempotent insert")
    row.username = tg.username
    row.first_name = tg.first_name
    row.last_name = tg.last_name
    await session.flush()
    return to_dto(row)


async def get_user(session: AsyncSession, user_id: int) -> UserDTO | None:
    row = await session.get(models.User, user_id)
    return None if row is None else to_dto(row)


async def ensure_member(session: AsyncSession, season_id: int, user_id: int, *, now: datetime) -> datetime:
    """Join the person to the season once; returns the moment they joined it."""
    row = await session.get(models.SeasonMember, (season_id, user_id))
    if row is None:
        row = models.SeasonMember(season_id=season_id, user_id=user_id, joined_at=now)
        session.add(row)
        await session.flush()
    return row.joined_at


async def member_joined_at(session: AsyncSession, season_id: int, user_id: int) -> datetime | None:
    """When the participant joined this season, or None when they never did."""
    row = await session.get(models.SeasonMember, (season_id, user_id))
    return None if row is None else row.joined_at


async def members(session: AsyncSession, season_id: int) -> dict[int, datetime]:
    """`{user_id: joined_at}` of a season, the input of every season-wide recomputation."""
    query = select(models.SeasonMember.user_id, models.SeasonMember.joined_at).where(
        models.SeasonMember.season_id == season_id
    )
    return dict((await session.execute(query)).tuples().all())


async def set_dialog_state(
    session: AsyncSession,
    user_id: int,
    state: str,
    payload: dict[str, Any] | None = None,
    *,
    now: datetime,
) -> None:
    """Remember what we are waiting for; any command or button clears it again."""
    row = await session.get(models.DialogState, user_id)
    if row is None:
        row = models.DialogState(user_id=user_id)
        session.add(row)
    row.state = state
    row.payload = payload or {}
    row.updated_at = now
    await session.flush()


async def get_dialog_state(session: AsyncSession, user_id: int, *, now: datetime) -> DialogStateDTO | None:
    """The state we are waiting for, or None when there is none or it went stale."""
    row = await session.get(models.DialogState, user_id)
    if row is None:
        return None
    if now - row.updated_at > DIALOG_TTL:
        await session.delete(row)
        await session.flush()
        return None
    return DialogStateDTO(user_id=row.user_id, state=row.state, payload=dict(row.payload), updated_at=row.updated_at)


async def clear_dialog_state(session: AsyncSession, user_id: int) -> None:
    row = await session.get(models.DialogState, user_id)
    if row is not None:
        await session.delete(row)
        await session.flush()


async def set_intent(
    session: AsyncSession,
    *,
    season_id: int,
    user_id: int,
    week_id: int,
    choice: models.IntentChoice,
    now: datetime,
) -> None:
    """«Берусь / Попробую / В этот раз мимо» under the weekly task; one row per week."""
    await ensure_member(session, season_id, user_id, now=now)
    query = select(models.WeekIntent).where(models.WeekIntent.user_id == user_id, models.WeekIntent.week_id == week_id)
    row = (await session.execute(query)).scalar_one_or_none()
    if row is None:
        row = models.WeekIntent(season_id=season_id, user_id=user_id, week_id=week_id)
        session.add(row)
    row.choice = choice.value
    row.updated_at = now
    await session.flush()


#: «try» is what buttons on old messages still send; it means the same as «take» (18.09.2026).
_INTENT_MEANING = {"take": "take", "try": "take", "skip": "skip"}


@dataclass(frozen=True)
class IntentOutcome:
    changed: bool
    """A new or a different answer: Mila is told. The same answer again: she was told already."""


async def choose_intent(
    session: AsyncSession,
    *,
    season_id: int,
    user_id: int,
    week: WeekDTO,
    choice: models.IntentChoice,
    today: date,
    now: datetime,
) -> IntentOutcome:
    """The rules of «берусь / мимо», shared by the bot button and the app (DOMAIN §2):
    only an announced week that is running, and only before the stamp.

    Refuses with the sentence for the person; the caller decides how to show it.
    """
    from romantika.texts import ru  # texts import UserDTO from here: a module-level import would loop

    if week.is_draft or week.starts_on > today:
        raise Refused(ru.INTENT_NOT_OPEN)
    if week.ends_on < today:
        raise Refused(ru.INTENT_WEEK_OVER)
    stamped = await session.execute(
        select(models.Stamp.id).where(models.Stamp.user_id == user_id, models.Stamp.week_id == week.id)
    )
    if stamped.first() is not None:
        raise Refused(ru.INTENT_ALREADY_STAMPED)
    # Two taps at once: the second waits, reads the first one's row and counts as a repeat.
    await locks.serialise(session, f"intent:{user_id}:{week.id}")
    previous = await get_intent(session, user_id=user_id, week_id=week.id)
    await set_intent(session, season_id=season_id, user_id=user_id, week_id=week.id, choice=choice, now=now)
    same = previous is not None and _INTENT_MEANING.get(previous) == _INTENT_MEANING[choice.value]
    return IntentOutcome(changed=not same)


async def get_intent(session: AsyncSession, *, user_id: int, week_id: int) -> str | None:
    """The person's answer on one week, if any («take» / «try» / «skip»)."""
    query = select(models.WeekIntent.choice).where(
        models.WeekIntent.user_id == user_id, models.WeekIntent.week_id == week_id
    )
    return (await session.execute(query)).scalar_one_or_none()


async def intents(session: AsyncSession, *, season_id: int, week_id: int) -> dict[int, models.IntentChoice]:
    """`{user_id: choice}` for one week, used by the summary and the reminders."""
    query = select(models.WeekIntent.user_id, models.WeekIntent.choice).where(
        models.WeekIntent.season_id == season_id, models.WeekIntent.week_id == week_id
    )
    return {user_id: models.IntentChoice(choice) for user_id, choice in (await session.execute(query)).all()}


async def all_users(session: AsyncSession) -> list[UserDTO]:
    """Everyone who ever wrote to the bot, oldest first (the admin's «кто в боте»)."""
    query = select(models.User).order_by(models.User.joined_at, models.User.id)
    return [to_dto(row) for row in (await session.execute(query)).scalars()]


async def display_names(session: AsyncSession, user_ids: Sequence[int], *, short: bool = False) -> dict[int, str]:
    """`{user_id: «Имя (@ник)»}` (or just the name with `short=True`) for the given people."""
    wanted = sorted({int(user_id) for user_id in user_ids})
    if not wanted:
        return {}
    query = select(models.User).where(models.User.id.in_(wanted))
    result: dict[int, str] = {}
    for row in (await session.execute(query)).scalars():
        dto = to_dto(row)
        result[dto.id] = dto.display_name if short else dto.display_name_with_username
    return result


async def find(session: AsyncSession, query_text: str) -> UserDTO | None:
    """By @username or by name: an exact match first, then a unique substring of the name.

    Compared in Python rather than SQL so that Cyrillic case folding works the same
    everywhere (the legacy bot had the same rule).
    """
    needle = query_text.strip().lstrip("@").lower()
    if not needle:
        return None
    rows = [to_dto(row) for row in (await session.execute(select(models.User))).scalars()]
    for dto in rows:
        if (dto.username or "").lower() == needle or dto.display_name.lower() == needle:
            return dto
    similar = [dto for dto in rows if needle in dto.display_name.lower()]
    return similar[0] if len(similar) == 1 else None
