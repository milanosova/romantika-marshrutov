"""Time-driven work: reminders and the backup health check (ARCHITECTURE §9.1, DOMAIN §8)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from romantika.domain.calendar import to_moscow
from romantika.services import content, jobs, reminders
from romantika.services.content import WeekDTO
from romantika.texts import ru

logger = logging.getLogger(__name__)

#: (weekday, hour, slug, text builder) — Moscow time; «≥ hour» so that a restart catches up.
SLOTS: list[tuple[int, int, str, Callable[[WeekDTO], str]]] = [
    (3, 19, "thu", ru.reminder_thursday),
    (6, 12, "sun", ru.reminder_sunday),
]
BACKUP_STALE_AFTER = timedelta(days=8)
#: The journals go out the day after the season ends, at noon Moscow time (DOMAIN §7).
JOURNALS_HOUR = 12


async def reminders_tick(
    session: AsyncSession,
    *,
    telegram: reminders.MessageSender,
    now: datetime,
    admin_chat: int | None = None,
) -> int:
    """Send the reminder of the current slot once per day; returns how many people got it."""
    if not await reminders.enabled(session):
        return 0
    local = to_moscow(now)
    season = await content.active_season(session, today=local.date())
    if season is None:
        return 0
    week = await content.current_week(session, season.id, today=local.date())
    if week is None:
        return 0

    sent_total = 0
    for weekday, hour, slug, text_for in SLOTS:
        if local.weekday() != weekday or local.hour < hour:
            continue
        key = f"{local.date().isoformat()}:{slug}"
        if not await reminders.mark_sent(session, key, now=now):
            continue
        result = await reminders.send(
            session, season_id=season.id, week_number=week.number, telegram=telegram, text_for=text_for, now=now
        )
        await reminders.record_recipients(session, key, result.total)
        sent_total += result.sent
        logger.info("reminders_sent", extra={"slot": slug, "sent": result.sent, "total": result.total})
        if admin_chat is not None:
            try:
                await telegram.send_message(admin_chat, f"⏰ Автонапоминание ({slug}). {result.describe()}")
            except Exception as exc:
                logger.warning("admin_note_failed", extra={"error": str(exc)})
    return sent_total


def read_verify_status(backups_dir: Path) -> dict[str, object] | None:
    path = backups_dir / "last-verify.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def backup_alert(status: dict[str, object] | None, *, now: datetime) -> str | None:
    """The alert text for the admin, or None when the last verification is fresh and green."""
    if status is None:
        return "🛑 Бэкапы: отчёта о проверке восстановления нет вовсе. Проверь контейнер backup."
    checked_at_raw = status.get("checked_at")
    try:
        checked_at = datetime.fromisoformat(str(checked_at_raw))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=now.tzinfo)
    except (TypeError, ValueError):
        return "🛑 Бэкапы: в отчёте о проверке нет даты. Проверь контейнер backup."
    if not status.get("ok"):
        errors = status.get("errors") or []
        joined = "; ".join(str(e) for e in errors) if isinstance(errors, list) else str(errors)
        return f"🛑 Бэкапы: проверка восстановления провалилась ({checked_at:%d.%m %H:%M}). {joined}".strip()
    if now - checked_at > BACKUP_STALE_AFTER:
        return f"⚠️ Бэкапы: последняя успешная проверка восстановления была {checked_at:%d.%m}, это больше 8 дней назад."
    return None


async def backup_status_tick(
    session: AsyncSession,
    *,
    telegram: reminders.MessageSender,
    backups_dir: Path,
    now: datetime,
    admin_chat: int | None = None,
) -> str | None:
    """Alert the admin when backups are missing, failing or stale. Returns the alert text."""
    alert = backup_alert(read_verify_status(backups_dir), now=now)
    if alert is None:
        return None
    if admin_chat is not None:
        try:
            await telegram.send_message(admin_chat, alert)
        except Exception as exc:
            logger.error("backup_alert_failed", extra={"error": str(exc), "alert": alert})
    else:
        logger.error("backup_alert_no_admin", extra={"alert": alert})
    return alert


async def season_end_tick(session: AsyncSession, *, now: datetime, admin_chat: int | None = None) -> bool:
    """Once, the day after the last day of the season: queue the journals for everyone.

    Deduplicated through `reminder_log` like the reminders (`<season slug>:journals`), so a
    restart never sends them twice. Returns True when the job was queued on this tick.
    """
    local = to_moscow(now)
    # A finished season stays active until Mila archives it (`content.active_season`), so the
    # day after its last day it is still the one this finds.
    season = await content.active_season(session, today=local.date())
    if season is None:
        return False
    if local.date() < season.ends_on + timedelta(days=1) or local.hour < JOURNALS_HOUR:
        return False
    key = f"{season.slug}:journals"
    if not await reminders.mark_sent(session, key, now=now):
        return False
    await jobs.enqueue(session, "season_journals", {"season_id": season.id, "admin_chat": admin_chat}, now=now)
    logger.info("season_journals_queued", extra={"season": season.slug})
    return True
