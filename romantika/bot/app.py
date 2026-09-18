"""Dispatcher factory (ARCHITECTURE §7.1)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import ErrorEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.bot.middlewares import ContextMiddleware
from romantika.bot.routers import admin_reply, callbacks, reports, user
from romantika.bot.send import safe_send
from romantika.config import Settings
from romantika.services.errors import Refused
from romantika.services.gateways import TelegramGateway
from romantika.services.media import MediaStore

logger = logging.getLogger(__name__)


async def _refused(event: ErrorEvent, bot: Bot) -> bool:
    """A service refused the input (`services.errors.Refused`): the person hears why, in
    Russian, the way the web answers 422 — never a silent traceback. The update's
    transaction has already rolled back, so a dialog state stays and the answer can be retried.
    """
    update = event.update
    message = update.message or (update.callback_query.message if update.callback_query else None)
    chat_id = message.chat.id if message is not None and hasattr(message, "chat") else None
    logger.info("refused", extra={"chat_id": chat_id, "reason": str(event.exception)})
    if chat_id is not None:
        await safe_send(bot, chat_id, str(event.exception))
    return True


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    media_store: MediaStore,
    *,
    telegram: TelegramGateway | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Dispatcher:
    dp = Dispatcher()
    dp.errors.register(_refused, ExceptionTypeFilter(Refused))
    dp.update.outer_middleware(
        ContextMiddleware(
            settings=settings,
            session_factory=session_factory,
            media_store=media_store,
            telegram=telegram,
            clock=clock,
        )
    )
    # Order matters: Mila's replies to forwarded reports first, then buttons, then commands
    # and dialog answers, and everything that is left is a report.
    dp.include_router(admin_reply.build())
    dp.include_router(callbacks.build())
    dp.include_router(user.build())
    dp.include_router(reports.build())
    return dp
