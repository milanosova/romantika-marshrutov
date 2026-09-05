"""Polling entrypoint: `python -m romantika.bot`."""

from __future__ import annotations

import asyncio
import logging

from romantika.bot.app import create_dispatcher
from romantika.bot.factory import make_bot
from romantika.config import get_settings
from romantika.db.session import make_session_factory
from romantika.logging import setup_logging
from romantika.services.media import MediaStore

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, json_output=settings.env != "dev")
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is not set")
    session_factory = make_session_factory(settings.database_url)
    dispatcher = create_dispatcher(settings, session_factory, MediaStore(settings.media_dir))
    bot = make_bot(settings)
    me = await bot.get_me()
    logger.info("bot_started", extra={"username": me.username, "admins": list(settings.admin_ids)})
    if not settings.admin_ids:
        logger.warning("no_admins_configured")
    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await dispatcher.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("bot_stopped")


if __name__ == "__main__":
    main()
