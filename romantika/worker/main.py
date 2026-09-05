"""`python -m romantika.worker`: jobs every 2 s, schedulers every 60 s, backup check hourly."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from romantika.bot.factory import make_bot
from romantika.bot.gateway import AiogramTelegramGateway
from romantika.config import Settings, get_settings
from romantika.db.session import make_session_factory
from romantika.logging import setup_logging
from romantika.services.media import MediaStore
from romantika.worker.runner import run_once
from romantika.worker.schedulers import backup_status_tick, reminders_tick, season_end_tick

logger = logging.getLogger(__name__)

JOB_INTERVAL = 2.0
SCHEDULER_INTERVAL = 60.0
BACKUP_CHECK_INTERVAL = 6 * 3600.0


def admin_chat_of(settings: Settings) -> int | None:
    return settings.admin_chat


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, json_output=settings.env != "dev")
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is not set")
    session_factory = make_session_factory(settings.database_url)
    media_store = MediaStore(settings.media_dir)
    bot = make_bot(settings)
    telegram = AiogramTelegramGateway(bot)
    admin_chat = admin_chat_of(settings)
    logger.info(
        "worker_started", extra={"media_dir": str(settings.media_dir), "backups_dir": str(settings.backups_dir)}
    )

    last_scheduler = 0.0
    last_backup_check = 0.0
    last_backup_alert: str | None = None
    loop = asyncio.get_running_loop()
    try:
        while True:
            now = datetime.now(UTC)
            try:
                kind = await run_once(session_factory, telegram=telegram, media_store=media_store, now=now)
            except Exception:
                logger.exception("run_once_crashed")
                kind = None

            monotonic = loop.time()
            if monotonic - last_scheduler >= SCHEDULER_INTERVAL:
                last_scheduler = monotonic
                try:
                    async with session_factory() as session, session.begin():
                        await reminders_tick(session, telegram=telegram, now=now, admin_chat=admin_chat)
                except Exception:
                    logger.exception("reminders_tick_crashed")
                try:
                    async with session_factory() as session, session.begin():
                        await season_end_tick(session, now=now, admin_chat=admin_chat)
                except Exception:
                    logger.exception("season_end_tick_crashed")
            if monotonic - last_backup_check >= BACKUP_CHECK_INTERVAL:
                last_backup_check = monotonic
                try:
                    async with session_factory() as session, session.begin():
                        # The same alert is not repeated on every check; a new text means a new problem.
                        alert = await backup_status_tick(
                            session,
                            telegram=telegram,
                            backups_dir=settings.backups_dir,
                            now=now,
                            admin_chat=admin_chat if last_backup_alert is None else None,
                        )
                        last_backup_alert = alert
                except Exception:
                    logger.exception("backup_status_tick_crashed")

            if kind is None:
                await asyncio.sleep(JOB_INTERVAL)
    finally:
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("worker_stopped")


if __name__ == "__main__":
    main()
