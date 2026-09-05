"""Import the legacy bot's SQLite (`данные.sqlite`) into Postgres (ARCHITECTURE §13, DOMAIN §9).

Idempotent by natural keys: re-running imports nothing that is already there. Media files are
downloaded from Telegram by `file_id` through the gateway, so the import must run while the
same bot token is alive. Naive legacy timestamps are Europe/Moscow.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.domain.types import ReportKind, StampLevel
from romantika.services import content, people, wishes, words
from romantika.services import media as media_service
from romantika.services.gateways import TelegramGateway
from romantika.services.media import MediaStore

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")

LEGACY_TABLES = (
    "люди",
    "берусь",
    "отчёты",
    "штампы",
    "свои_слова",
    "ачивки",
    "пожелания",
    "заморозки",
    "правки_недель",
    "факты",
)
INTENTS = {"берусь": "take", "попробую": "try", "мимо": "skip"}
LEVELS = {"максимум": StampLevel.MAX, "минимум": StampLevel.MIN}
FREEZE_REASONS = {"слово": "word", "максимум": "max", "коммент": "comment", "встреча": "meetup", "друг": "friend"}
WEEK_FIELDS = {
    "title": "title",
    "intro": "intro",
    "minimum": "task_min",
    "maximum": "task_max",
    "word": "word",
    "word_ru": "word_ru",
    "word_meaning": "word_meaning",
}
KINDS = {kind.value: kind for kind in ReportKind}


@dataclass
class ImportReport:
    legacy_counts: dict[str, int] = field(default_factory=dict)
    imported: dict[str, int] = field(default_factory=dict)

    def bump(self, key: str, by: int = 1) -> None:
        self.imported[key] = self.imported.get(key, 0) + by

    def table(self) -> str:
        lines = ["legacy table      rows   |  imported now"]
        pairs = [
            ("люди", "users"), ("берусь", "intents"), ("отчёты", "reports"), ("(файлы)", "media"),
            ("штампы", "stamps"), ("заморозки", "freezes"), ("ачивки", "achievements"),
            ("свои_слова", "words"), ("факты", "facts"), ("пожелания", "wishes"), ("правки_недель", "week_overrides"),
        ]  # fmt: skip
        for legacy, key in pairs:
            lines.append(
                f"{legacy:<16} {self.legacy_counts.get(legacy, '-')!s:>6}   |  {key}: {self.imported.get(key, 0)}"
            )
        return "\n".join(lines)


def _moscow(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = datetime.fromisoformat(str(raw))
    if value.tzinfo is None:
        value = value.replace(tzinfo=MOSCOW)
    return value.astimezone(UTC)


def _open(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _count(connection: sqlite3.Connection, table: str) -> int:
    try:
        return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except sqlite3.OperationalError:
        return 0


async def import_legacy(
    session: AsyncSession,
    *,
    sqlite_path: Path,
    season_id: int,
    media_store: MediaStore,
    telegram: TelegramGateway,
    now: datetime,
    download: bool = True,
) -> ImportReport:
    report = ImportReport()
    season = await content.require_season(session, season_id)
    weeks = {week.number: week for week in await content.weeks(session, season_id)}
    connection = _open(sqlite_path)
    try:
        for table in LEGACY_TABLES:
            report.legacy_counts[table] = _count(connection, table)
        report.imported = dict.fromkeys(
            (
                "users", "intents", "reports", "media", "stamps", "freezes",
                "achievements", "words", "facts", "wishes", "week_overrides",
            ),
            0,
        )  # fmt: skip

        # --- people ------------------------------------------------------------------
        for row in connection.execute("SELECT id, ник, имя, пришёл FROM люди ORDER BY id"):
            user_id = int(row["id"])
            joined = _moscow(row["пришёл"]) or now
            existing = await session.get(models.User, user_id)
            if existing is None:
                session.add(
                    models.User(
                        id=user_id,
                        username=row["ник"] or None,
                        first_name=row["имя"] or None,
                        joined_at=joined,
                        created_at=joined,
                    )
                )
                await session.flush()
                report.bump("users")
            if await people.member_joined_at(session, season_id, user_id) is None:
                await people.ensure_member(session, season_id, user_id, now=joined)

        # --- intents -------------------------------------------------------------------
        for row in connection.execute("SELECT человек, неделя, выбор, когда FROM берусь"):
            week = weeks.get(int(row["неделя"]))
            choice = INTENTS.get(str(row["выбор"]))
            if week is None or choice is None:
                continue
            exists = await session.scalar(
                select(models.WeekIntent.id).where(
                    models.WeekIntent.user_id == int(row["человек"]), models.WeekIntent.week_id == week.id
                )
            )
            if exists is not None:
                continue
            await people.set_intent(
                session,
                season_id=season_id,
                user_id=int(row["человек"]),
                week_id=week.id,
                choice=models.IntentChoice(choice),
                now=_moscow(row["когда"]) or now,
            )
            report.bump("intents")

        # --- reports and media ---------------------------------------------------------
        for row in connection.execute(
            "SELECT id, человек, неделя, вид, текст, файл, уровень, когда FROM отчёты ORDER BY id"
        ):
            week = weeks.get(int(row["неделя"])) if row["неделя"] is not None else None
            kind = KINDS.get(str(row["вид"] or "text"), ReportKind.OTHER)
            created_at = _moscow(row["когда"]) or now
            user_id = int(row["человек"])
            exists = await session.scalar(
                select(models.Report.id).where(
                    models.Report.user_id == user_id,
                    models.Report.week_id == (week.id if week else None),
                    models.Report.created_at == created_at,
                    models.Report.kind == kind.value,
                )
            )
            if exists is not None:
                continue
            level = LEVELS.get(str(row["уровень"]), StampLevel.MIN)
            db_report = models.Report(
                season_id=season_id,
                user_id=user_id,
                week_id=week.id if week else None,
                kind=kind.value,
                text=row["текст"],
                level=level.value,
                created_at=created_at,
            )
            session.add(db_report)
            await session.flush()
            report.bump("reports")
            if row["файл"]:
                media_row = models.Media(
                    report_id=db_report.id,
                    tg_file_id=str(row["файл"]),
                    path=media_service.new_relative_path(
                        season_slug=season.slug, user_id=user_id, suffix=media_service.suffix_for(kind=kind, mime=None)
                    ),
                    created_at=created_at,
                )
                session.add(media_row)
                await session.flush()
                if download:
                    try:
                        await media_store.download(session, media_row.id, telegram, now=now)
                        report.bump("media")
                    except Exception as exc:
                        logger.warning(
                            "legacy_media_download_failed", extra={"file_id": row["файл"], "error": str(exc)}
                        )
                else:
                    report.bump("media")

        # --- stamps ----------------------------------------------------------------------
        for row in connection.execute("SELECT человек, неделя, уровень, когда, название FROM штампы"):
            week = weeks.get(int(row["неделя"]))
            if week is None:
                continue
            exists = await session.scalar(
                select(models.Stamp.id).where(
                    models.Stamp.user_id == int(row["человек"]), models.Stamp.week_id == week.id
                )
            )
            if exists is not None:
                continue
            session.add(
                models.Stamp(
                    season_id=season_id,
                    user_id=int(row["человек"]),
                    week_id=week.id,
                    level=LEVELS.get(str(row["уровень"]), StampLevel.MIN).value,
                    week_title_snapshot=row["название"] or week.title,
                    awarded_at=_moscow(row["когда"]) or now,
                    source=models.StampSource.REPORT.value,
                    created_at=_moscow(row["когда"]) or now,
                )
            )
            report.bump("stamps")
        await session.flush()

        # --- freezes ------------------------------------------------------------------------
        for row in connection.execute("SELECT человек, причина, когда FROM заморозки ORDER BY id"):
            reason = FREEZE_REASONS.get(str(row["причина"]), "manual")
            created_at = _moscow(row["когда"]) or now
            exists = await session.scalar(
                select(models.Freeze.id).where(
                    models.Freeze.season_id == season_id,
                    models.Freeze.user_id == int(row["человек"]),
                    models.Freeze.reason == reason,
                    models.Freeze.created_at == created_at,
                )
            )
            if exists is not None:
                continue
            session.add(
                models.Freeze(season_id=season_id, user_id=int(row["человек"]), reason=reason, created_at=created_at)
            )
            report.bump("freezes")
        await session.flush()

        # --- achievements ----------------------------------------------------------------
        for row in connection.execute("SELECT человек, код, подпись, когда FROM ачивки"):
            code = str(row["код"])[:64]
            exists = await session.scalar(
                select(models.Achievement.id).where(
                    models.Achievement.season_id == season_id,
                    models.Achievement.user_id == int(row["человек"]),
                    models.Achievement.code == code,
                )
            )
            if exists is not None:
                continue
            awarded = _moscow(row["когда"]) or now
            session.add(
                models.Achievement(
                    season_id=season_id,
                    user_id=int(row["человек"]),
                    code=code,
                    label=row["подпись"] or code,
                    awarded_at=awarded,
                    created_at=awarded,
                )
            )
            report.bump("achievements")
        await session.flush()

        # --- words ------------------------------------------------------------------------
        for row in connection.execute("SELECT человек, слово, значение, неделя, когда FROM свои_слова ORDER BY id"):
            raw = str(row["слово"] or "")
            if row["значение"]:
                raw = f"{raw} — {row['значение']}"
            word, meaning = words.parse(raw)
            if not word:
                continue
            exists = await session.scalar(
                select(models.Word.id).where(
                    models.Word.season_id == season_id,
                    models.Word.user_id == int(row["человек"]),
                    models.Word.word == word,
                    models.Word.meaning == meaning,
                )
            )
            if exists is not None:
                continue
            week = weeks.get(int(row["неделя"])) if row["неделя"] is not None else None
            session.add(
                models.Word(
                    season_id=season_id,
                    user_id=int(row["человек"]),
                    week_id=week.id if week else None,
                    word=word,
                    meaning=meaning,
                    created_at=_moscow(row["когда"]) or now,
                )
            )
            report.bump("words")
        await session.flush()

        # --- facts -------------------------------------------------------------------------
        for row in connection.execute("SELECT неделя, текст, когда, автор FROM факты ORDER BY id"):
            created_at = _moscow(row["когда"]) or now
            text = str(row["текст"] or "").strip()
            if not text:
                continue
            exists = await session.scalar(
                select(models.Fact.id).where(
                    models.Fact.season_id == season_id, models.Fact.text == text, models.Fact.created_at == created_at
                )
            )
            if exists is not None:
                continue
            week = weeks.get(int(row["неделя"])) if row["неделя"] is not None else None
            session.add(
                models.Fact(
                    season_id=season_id,
                    week_id=week.id if week else None,
                    text=text,
                    author_id=int(row["автор"]) if row["автор"] is not None else None,
                    created_at=created_at,
                )
            )
            report.bump("facts")
        await session.flush()

        # --- wishes -------------------------------------------------------------------------
        for row in connection.execute("SELECT человек, текст, когда FROM пожелания"):
            if not row["текст"]:
                continue
            if await wishes.get_wish(session, season_id, int(row["человек"])) is not None:
                continue
            await wishes.set_wish(
                session,
                season_id=season_id,
                user_id=int(row["человек"]),
                text=str(row["текст"]),
                now=_moscow(row["когда"]) or now,
            )
            report.bump("wishes")

        # --- week overrides ---------------------------------------------------------------
        for row in connection.execute("SELECT неделя, поле, значение FROM правки_недель"):
            week = weeks.get(int(row["неделя"]))
            column = WEEK_FIELDS.get(str(row["поле"]))
            if week is None or column is None:
                continue
            value = str(row["значение"] or "")
            if getattr(week, column) == value:
                continue
            updated = await content.update_week(session, actor_id=None, week_id=week.id, changes={column: value})
            weeks[week.number] = updated
            report.bump("week_overrides")
    finally:
        connection.close()
    return report


async def _main(args: argparse.Namespace) -> None:
    from romantika.bot.factory import make_bot
    from romantika.bot.gateway import AiogramTelegramGateway
    from romantika.config import get_settings
    from romantika.db.session import make_session_factory
    from romantika.logging import setup_logging

    settings = get_settings()
    setup_logging(settings.log_level)
    factory = make_session_factory(settings.database_url)
    bot = make_bot(settings)
    try:
        async with factory() as session, session.begin():
            season = await session.scalar(select(models.Season).where(models.Season.slug == args.season_slug))
            if season is None:
                raise SystemExit(f"season {args.season_slug!r} is not in the database; run the seed first")
            report = await import_legacy(
                session,
                sqlite_path=Path(args.sqlite),
                season_id=season.id,
                media_store=MediaStore(settings.media_dir),
                telegram=AiogramTelegramGateway(bot),
                now=datetime.now(UTC),
                download=not args.no_download,
            )
            if args.dry_run:
                await session.rollback()
        print(report.table())
    finally:
        await bot.session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the legacy SQLite database into Postgres")
    parser.add_argument("--sqlite", required=True, help="path to данные.sqlite")
    parser.add_argument("--season-slug", default="mexico-2026")
    parser.add_argument("--no-download", action="store_true", help="do not fetch media from Telegram")
    parser.add_argument("--dry-run", action="store_true", help="report only, roll back at the end")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
