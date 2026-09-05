"""Per-update context: DB session, the person, the active season, time, gateways."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, Update, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.bot.gateway import AiogramTelegramGateway
from romantika.config import Settings
from romantika.domain.calendar import moscow_now, to_moscow
from romantika.services import content, people
from romantika.services.gateways import TelegramGateway
from romantika.services.media import MediaStore
from romantika.services.people import TelegramUser

logger = logging.getLogger(__name__)


def _event_user(update: Update) -> User | None:
    if update.message is not None:
        return update.message.from_user
    if update.callback_query is not None:
        return update.callback_query.from_user
    if update.edited_message is not None:
        return update.edited_message.from_user
    return None


class ContextMiddleware(BaseMiddleware):
    """One transaction per update; handlers get the session and the ready-made context.

    Handler kwargs: `session`, `settings`, `now`, `today`, `user`, `season`, `is_admin`,
    `dialog`, `media_store`, `telegram`, `clock`, `admin_chat`.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        media_store: MediaStore,
        telegram: TelegramGateway | None,
        clock: Callable[[], datetime] | None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.media_store = media_store
        self.telegram = telegram
        self.clock = clock or moscow_now

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        tg_user = _event_user(event)
        if tg_user is None or tg_user.is_bot:
            return None

        now = self.clock()
        today = to_moscow(now).date()
        bot: Bot = data["bot"]
        async with self.session_factory() as session:
            async with session.begin():
                user = await people.upsert_user(
                    session,
                    TelegramUser(
                        id=tg_user.id,
                        username=tg_user.username,
                        first_name=tg_user.first_name,
                        last_name=tg_user.last_name,
                    ),
                    now=now,
                )
                season = await content.active_season(session, today=today)
                if season is not None:
                    await people.ensure_member(session, season.id, user.id, now=now)
                is_admin = user.is_admin or self.settings.is_admin(user.id)
                admin_chat = self.settings.admin_chat
                data.update(
                    session=session,
                    settings=self.settings,
                    now=now,
                    today=today,
                    clock=self.clock,
                    user=user,
                    season=season,
                    is_admin=is_admin,
                    admin_chat=admin_chat,
                    dialog=await people.get_dialog_state(session, user.id, now=now),
                    media_store=self.media_store,
                    telegram=self.telegram or AiogramTelegramGateway(bot),
                )
                return await handler(event, data)
