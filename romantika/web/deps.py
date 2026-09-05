"""FastAPI dependencies: app state, DB session per request, the authenticated person."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.config import Settings
from romantika.domain.calendar import to_moscow
from romantika.services import content, people
from romantika.services.content import SeasonDTO
from romantika.services.media import MediaStore
from romantika.services.people import TelegramUser, UserDTO
from romantika.web import auth


@dataclass(frozen=True, slots=True)
class AppState:
    settings: Settings
    session_factory: Callable[[], AsyncSession]
    media_store: MediaStore
    clock: Callable[[], datetime]


def state_of(request: Request) -> AppState:
    state: AppState = request.app.state.romantika
    return state


def get_settings(request: Request) -> Settings:
    return state_of(request).settings


def get_media_store(request: Request) -> MediaStore:
    return state_of(request).media_store


def get_now(request: Request) -> datetime:
    return state_of(request).clock()


def get_today(now: Annotated[datetime, Depends(get_now)]) -> date:
    return to_moscow(now).date()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request: committed on success, rolled back on any error."""
    factory = state_of(request).session_factory
    async with factory() as session:
        async with session.begin():
            yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
NowDep = Annotated[datetime, Depends(get_now)]
TodayDep = Annotated[date, Depends(get_today)]
MediaStoreDep = Annotated[MediaStore, Depends(get_media_store)]


@dataclass(frozen=True, slots=True)
class Principal:
    user: UserDTO
    is_admin: bool
    via_cookie: bool


async def optional_principal(
    request: Request, session: SessionDep, settings: SettingsDep, now: NowDep
) -> Principal | None:
    """The person behind the request, or None; never raises."""
    header = request.headers.get(auth.INIT_DATA_HEADER)
    if header:
        info = auth.validate_init_data(header, settings.bot_token, now=now)
        if info is None:
            return None
        user = await people.upsert_user(
            session,
            TelegramUser(id=info.id, username=info.username, first_name=info.first_name, last_name=info.last_name),
            now=now,
        )
        return Principal(user=user, is_admin=user.is_admin or settings.is_admin(user.id), via_cookie=False)

    if settings.env == "dev" and settings.dev_auth_user_id is not None and request.headers.get("X-Dev-Auth") == "1":
        user = await people.upsert_user(session, TelegramUser(id=settings.dev_auth_user_id, first_name="Dev"), now=now)
        return Principal(user=user, is_admin=user.is_admin or settings.is_admin(user.id), via_cookie=False)

    user_id = auth.read_session_token(request.cookies.get(auth.SESSION_COOKIE), settings.bot_token, now=now)
    if user_id is None:
        return None
    existing = await people.get_user(session, user_id)
    if existing is None:
        return None
    return Principal(user=existing, is_admin=existing.is_admin or settings.is_admin(existing.id), via_cookie=True)


async def current_principal(principal: Annotated[Principal | None, Depends(optional_principal)]) -> Principal:
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Telegram init data is missing or invalid")
    return principal


async def admin_principal(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return principal


PrincipalDep = Annotated[Principal, Depends(current_principal)]
AdminDep = Annotated[Principal, Depends(admin_principal)]


async def active_season(session: SessionDep, today: TodayDep) -> SeasonDTO:
    season = await content.active_season(session, today=today)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active season")
    return season


SeasonDep = Annotated[SeasonDTO, Depends(active_season)]
