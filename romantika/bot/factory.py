"""Build the aiogram `Bot` the way every process needs it (proxy, timeouts, API base)."""

from __future__ import annotations

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from romantika.config import Settings


def make_bot(settings: Settings) -> Bot:
    """A Bot that reaches Telegram through `TELEGRAM_PROXY` (or `HTTPS_PROXY`) when set.

    aiohttp does not read proxy variables from the environment on its own; on the VPS
    Telegram is reachable only through the host proxy, so this is not optional there.
    `TELEGRAM_API_BASE` points every process at the local stand's fake Bot API instead.
    """
    proxy = settings.telegram_proxy or None
    session = AiohttpSession(proxy=proxy, timeout=60) if proxy else AiohttpSession(timeout=60)
    if settings.telegram_api_base:
        if settings.env != "dev":
            raise RuntimeError("TELEGRAM_API_BASE is for the local stand only (ENV=dev)")
        session.api = TelegramAPIServer.from_base(settings.telegram_api_base.rstrip("/"))
    return Bot(settings.bot_token, session=session)
