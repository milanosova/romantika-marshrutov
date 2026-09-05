"""Dispatcher factory (ARCHITECTURE §7.1)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.bot.middlewares import ContextMiddleware
from romantika.bot.routers import admin_reply, callbacks, reports, user
from romantika.config import Settings
from romantika.services.gateways import TelegramGateway
from romantika.services.media import MediaStore


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    media_store: MediaStore,
    *,
    telegram: TelegramGateway | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Dispatcher:
    dp = Dispatcher()
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
