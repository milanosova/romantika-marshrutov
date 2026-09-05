"""Stage 6 acceptance: legacy import, backup + restore verification (ARCHITECTURE §11, §13).

READ-ONLY for implementers. Contract used here:
- `romantika.migration.legacy_import.import_legacy(session, *, sqlite_path, season_id, media_store,
  telegram, now) -> ImportReport` with per-table counts `imported: dict[str, int]` and
  `legacy_counts: dict[str, int]`; idempotent.
- `scripts/backup.sh` and `scripts/restore-verify.sh` are driven by environment variables
  `DATABASE_URL` (SQLAlchemy URL, `+asyncpg` allowed), `MEDIA_DIR`, `BACKUP_DIR`,
  `RETENTION_DAYS`, and for restore-verify `SCRATCH_DATABASE_URL` (a database the script may
  drop and recreate). `pg_dump`/`pg_restore`/`psql` come from PATH (`PG_BIN` prefix optional).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.db import models
from romantika.db.session import make_engine
from romantika.migration.legacy_import import import_legacy
from romantika.services import content, seed
from romantika.services.gateways import TelegramFile
from romantika.services.media import MediaStore

REPO = Path(__file__).resolve().parents[2]
SEASON_JSON = REPO / "data" / "seasons" / "mexico-2026.json"
ADMIN_ID = 355363829
PG_BIN = os.environ.get("PG_BIN") or ("/opt/homebrew/opt/libpq/bin" if Path("/opt/homebrew/opt/libpq/bin/pg_dump").exists() else "")

LEGACY_SCHEMA = """
CREATE TABLE люди (id INTEGER PRIMARY KEY, ник TEXT, имя TEXT, пришёл TEXT);
CREATE TABLE берусь (человек INTEGER, неделя INTEGER, выбор TEXT, когда TEXT, PRIMARY KEY (человек, неделя));
CREATE TABLE отчёты (id INTEGER PRIMARY KEY AUTOINCREMENT, человек INTEGER, неделя INTEGER, вид TEXT, текст TEXT, файл TEXT, уровень TEXT, когда TEXT, название TEXT);
CREATE TABLE штампы (человек INTEGER, неделя INTEGER, уровень TEXT, когда TEXT, название TEXT, PRIMARY KEY (человек, неделя));
CREATE TABLE служебное (ключ TEXT PRIMARY KEY, значение TEXT);
CREATE TABLE связи (сообщение INTEGER PRIMARY KEY, автор INTEGER, неделя INTEGER);
CREATE TABLE свои_слова (id INTEGER PRIMARY KEY AUTOINCREMENT, человек INTEGER, слово TEXT, значение TEXT, неделя INTEGER, когда TEXT);
CREATE TABLE ачивки (человек INTEGER, код TEXT, подпись TEXT, когда TEXT, PRIMARY KEY (человек, код));
CREATE TABLE пожелания (человек INTEGER PRIMARY KEY, текст TEXT, когда TEXT);
CREATE TABLE заморозки (id INTEGER PRIMARY KEY AUTOINCREMENT, человек INTEGER, причина TEXT, когда TEXT);
CREATE TABLE правки_недель (неделя INTEGER, поле TEXT, значение TEXT, когда TEXT, PRIMARY KEY (неделя, поле));
CREATE TABLE факты (id INTEGER PRIMARY KEY AUTOINCREMENT, неделя INTEGER, текст TEXT, когда TEXT, автор INTEGER);
"""


def build_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.executescript(
        """
        INSERT INTO люди VALUES (1001, 'alice', 'Алиса', '2026-08-20T10:00:00');
        INSERT INTO люди VALUES (1002, NULL, 'Боб', '2026-09-09T12:00:00');
        INSERT INTO берусь VALUES (1001, 1, 'берусь', '2026-08-31T09:00:00');
        INSERT INTO берусь VALUES (1002, 2, 'попробую', '2026-09-08T09:00:00');
        INSERT INTO отчёты (человек, неделя, вид, текст, файл, уровень, когда) VALUES (1001, 1, 'text', 'сварила кофе', NULL, 'минимум', '2026-09-01T18:00:00');
        INSERT INTO отчёты (человек, неделя, вид, текст, файл, уровень, когда) VALUES (1001, 1, 'photo', 'тако', 'AgACAgIAAxkBAAI', 'максимум', '2026-09-02T18:00:00');
        INSERT INTO отчёты (человек, неделя, вид, текст, файл, уровень, когда) VALUES (1002, 2, 'video_note', NULL, 'DQACAgIAAxkBAAI', 'максимум', '2026-09-10T18:00:00');
        INSERT INTO штампы VALUES (1001, 1, 'максимум', '2026-09-02T18:00:00', 'За столом');
        INSERT INTO штампы VALUES (1002, 2, 'максимум', '2026-09-10T18:00:00', 'Красками');
        INSERT INTO служебное VALUES ('смещение', '123456');
        INSERT INTO служебное VALUES ('напоминания', 'выкл');
        INSERT INTO служебное VALUES ('напомнили:2026-09-03:четверг', '1');
        INSERT INTO связи VALUES (500, 1001, 1);
        INSERT INTO свои_слова (человек, слово, значение, неделя, когда) VALUES (1001, 'sobremesa — время за столом после еды', '', 1, '2026-09-03T10:00:00');
        INSERT INTO ачивки VALUES (1001, 'повар', '🌮 Повар', '2026-09-03T10:00:00');
        INSERT INTO ачивки VALUES (1001, 'самый ранний отчёт', 'Самый ранний отчёт', '2026-09-03T10:00:00');
        INSERT INTO пожелания VALUES (1001, 'Ты молодец', '2026-09-03T10:00:00');
        INSERT INTO заморозки (человек, причина, когда) VALUES (1001, 'максимум', '2026-09-02T18:00:00');
        INSERT INTO заморозки (человек, причина, когда) VALUES (1001, 'коммент', '2026-09-04T18:00:00');
        INSERT INTO правки_недель VALUES (1, 'title', 'За столом (исправлено)', '2026-08-30T10:00:00');
        INSERT INTO правки_недель VALUES (3, 'minimum', 'Новый минимум третьей недели', '2026-09-10T10:00:00');
        INSERT INTO факты (неделя, текст, когда, автор) VALUES (1, 'Ацтеки называли себя мешика', '2026-09-03T10:00:00', NULL);
        INSERT INTO факты (неделя, текст, когда, автор) VALUES (1, 'Какао было валютой', '2026-09-03T11:00:00', 1001);
        """
    )
    conn.commit()
    conn.close()


@dataclass
class FakeTelegram:
    payload: bytes = b"fake-jpeg-bytes"
    calls: list[str] = field(default_factory=list)

    async def get_file(self, file_id: str) -> TelegramFile:
        return TelegramFile(file_path=f"files/{file_id}.bin", file_size=len(self.payload))

    async def download_file(self, file_path: str, destination: Path) -> None:
        self.calls.append(file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payload)


# --- legacy import --------------------------------------------------------------------


async def test_legacy_import_maps_every_table_and_is_idempotent(db_session: AsyncSession, tmp_path: Path) -> None:
    result = await seed.import_season(db_session, SEASON_JSON)
    await content.activate_season(db_session, result.season_id, actor_id=ADMIN_ID)
    legacy = tmp_path / "данные.sqlite"
    build_legacy_db(legacy)
    store = MediaStore(tmp_path / "media")
    telegram = FakeTelegram()
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)

    report = await import_legacy(db_session, sqlite_path=legacy, season_id=result.season_id, media_store=store, telegram=telegram, now=now)
    assert report.legacy_counts == {
        "люди": 2, "берусь": 2, "отчёты": 3, "штампы": 2, "свои_слова": 1, "ачивки": 2,
        "пожелания": 1, "заморозки": 2, "правки_недель": 2, "факты": 2,
    }  # fmt: skip
    assert report.imported["users"] == 2 and report.imported["reports"] == 3 and report.imported["media"] == 2
    assert report.imported["stamps"] == 2 and report.imported["freezes"] == 2 and report.imported["achievements"] == 2
    assert report.imported["words"] == 1 and report.imported["facts"] == 2 and report.imported["wishes"] == 1
    assert report.imported["intents"] == 2 and report.imported["week_overrides"] == 2
    assert sorted(telegram.calls) == ["files/AgACAgIAAxkBAAI.bin", "files/DQACAgIAAxkBAAI.bin"]

    alice = await db_session.get(models.User, 1001)
    assert alice is not None and alice.username == "alice"
    assert alice.joined_at == datetime(2026, 8, 20, 7, 0, tzinfo=UTC), "legacy naive Moscow time → UTC"
    member = await db_session.get(models.SeasonMember, (result.season_id, 1002))
    assert member is not None and member.joined_at == datetime(2026, 9, 9, 9, 0, tzinfo=UTC)

    stamps = {s.user_id: s for s in (await db_session.execute(select(models.Stamp))).scalars().all()}
    assert stamps[1001].level == "max" and stamps[1001].week_title_snapshot == "За столом"
    week1 = (await db_session.execute(select(models.Week).where(models.Week.number == 1))).scalar_one()
    week3 = (await db_session.execute(select(models.Week).where(models.Week.number == 3))).scalar_one()
    assert week1.title == "За столом (исправлено)" and week3.task_min == "Новый минимум третьей недели"
    assert (await db_session.execute(select(func.count()).select_from(models.AuditLog).where(models.AuditLog.entity == "week"))).scalar_one() == 2

    media = (await db_session.execute(select(models.Media))).scalars().all()
    assert len(media) == 2 and all(m.downloaded_at is not None and (store.root / m.path).exists() for m in media)
    kinds = {r.kind for r in (await db_session.execute(select(models.Report))).scalars().all()}
    assert kinds == {"text", "photo", "video_note"}

    word = (await db_session.execute(select(models.Word))).scalar_one()
    assert (word.word, word.meaning) == ("sobremesa", "время за столом после еды")
    reasons = sorted(f.reason for f in (await db_session.execute(select(models.Freeze))).scalars().all())
    assert reasons == ["comment", "max"]
    facts = (await db_session.execute(select(models.Fact).order_by(models.Fact.id))).scalars().all()
    assert facts[0].author_id is None and facts[1].author_id == 1001
    wish = (await db_session.execute(select(models.Wish))).scalar_one()
    assert wish.text == "Ты молодец"
    intents = {i.user_id: i.choice for i in (await db_session.execute(select(models.WeekIntent))).scalars().all()}
    assert intents == {1001: "take", 1002: "try"}

    again = await import_legacy(db_session, sqlite_path=legacy, season_id=result.season_id, media_store=store, telegram=telegram, now=now)
    assert all(v == 0 for v in again.imported.values()), f"second run must import nothing: {again.imported}"
    assert (await db_session.execute(select(func.count()).select_from(models.Report))).scalar_one() == 3
    assert len(telegram.calls) == 2


# --- backup and restore verification -----------------------------------------------------


def _pg(cmd: str) -> str:
    return str(Path(PG_BIN) / cmd) if PG_BIN else cmd


def _admin_url(database_url: str) -> str:
    base = database_url.split("://", 1)[1].replace("+asyncpg", "")
    return "postgresql://" + base.rsplit("/", 1)[0] + "/postgres"


async def _create_database(database_url: str, name: str) -> str:
    admin = await asyncpg.connect(_admin_url(database_url))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    return database_url.rsplit("/", 1)[0] + "/" + name


@pytest.mark.skipif(shutil.which(_pg("pg_dump")) is None and not Path(_pg("pg_dump")).exists(), reason="pg_dump not installed")
async def test_backup_then_restore_verify(database_url: str, tmp_path: Path) -> None:
    import asyncio

    from tests.conftest import run_alembic

    live_url = await _create_database(database_url, "romantika_backup_live")
    scratch_url = await _create_database(database_url, "romantika_backup_scratch")
    await asyncio.to_thread(run_alembic, live_url, "head")  # alembic's env.py runs its own event loop

    media_dir = tmp_path / "media"
    (media_dir / "mexico-2026" / "1001").mkdir(parents=True)
    (media_dir / "mexico-2026" / "1001" / "a.jpg").write_bytes(b"photo-a")
    engine = make_engine(live_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        result = await seed.import_season(session, SEASON_JSON)
        session.add(models.User(id=1001, first_name="Алиса"))
        await session.flush()
        week = (await session.execute(select(models.Week).where(models.Week.number == 1))).scalar_one()
        report = models.Report(season_id=result.season_id, user_id=1001, week_id=week.id, kind="photo", text="тако", level="max")
        session.add(report)
        await session.flush()
        import hashlib

        session.add(
            models.Media(report_id=report.id, tg_file_id="AAA", mime="image/jpeg", size=7, sha256=hashlib.sha256(b"photo-a").hexdigest(), path="mexico-2026/1001/a.jpg", downloaded_at=datetime.now(UTC))
        )
        session.add(models.Stamp(season_id=result.season_id, user_id=1001, week_id=week.id, level="max", week_title_snapshot="За столом"))
        await session.commit()
    await engine.dispose()

    backup_dir = tmp_path / "backups"
    env = {
        **os.environ,
        "DATABASE_URL": live_url,
        "MEDIA_DIR": str(media_dir),
        "BACKUP_DIR": str(backup_dir),
        "RETENTION_DAYS": "30",
        "SCRATCH_DATABASE_URL": scratch_url,
        "PATH": (PG_BIN + os.pathsep if PG_BIN else "") + os.environ["PATH"],
        "TODAY": "2026-09-12",
    }
    backup = subprocess.run(["bash", str(REPO / "scripts" / "backup.sh")], env=env, capture_output=True, text=True, timeout=300)
    assert backup.returncode == 0, backup.stdout + backup.stderr
    dump = backup_dir / "db" / "romantika-2026-09-12.dump"
    assert dump.exists() and dump.stat().st_size > 1000
    assert (backup_dir / "media" / "2026-09-12" / "mexico-2026" / "1001" / "a.jpg").read_bytes() == b"photo-a"
    manifest = json.loads((backup_dir / "manifest-2026-09-12.json").read_text())
    assert manifest["tables"]["stamps"] == 1 and manifest["tables"]["media"] == 1 and manifest["media_files"] == 1
    assert manifest["dump_sha256"] and len(manifest["dump_sha256"]) == 64

    verify = subprocess.run(["bash", str(REPO / "scripts" / "restore-verify.sh")], env=env, capture_output=True, text=True, timeout=300)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    status = json.loads((backup_dir / "last-verify.json").read_text())
    assert status["ok"] is True and status["dump"] == "romantika-2026-09-12.dump"
    assert status["tables"]["stamps"] == 1 and status["media_checked"] == 1 and status["errors"] == []

    # Corrupt the media snapshot → verification must fail loudly.
    (backup_dir / "media" / "2026-09-12" / "mexico-2026" / "1001" / "a.jpg").write_bytes(b"corrupted")
    verify = subprocess.run(["bash", str(REPO / "scripts" / "restore-verify.sh")], env=env, capture_output=True, text=True, timeout=300)
    assert verify.returncode != 0
    status = json.loads((backup_dir / "last-verify.json").read_text())
    assert status["ok"] is False and status["errors"]

    # Retention: an old dump is removed, today's stays.
    old = backup_dir / "db" / "romantika-2026-07-01.dump"
    old.write_bytes(b"old")
    (backup_dir / "media" / "2026-07-01").mkdir()
    backup = subprocess.run(["bash", str(REPO / "scripts" / "backup.sh")], env=env, capture_output=True, text=True, timeout=300)
    assert backup.returncode == 0, backup.stdout + backup.stderr
    assert not old.exists() and dump.exists() and not (backup_dir / "media" / "2026-07-01").exists()

    admin = await asyncpg.connect(_admin_url(database_url))
    try:
        await admin.execute('DROP DATABASE IF EXISTS "romantika_backup_live"')
        await admin.execute('DROP DATABASE IF EXISTS "romantika_backup_scratch"')
    finally:
        await admin.close()


def test_scripts_never_delete_media_or_rows() -> None:
    """Static guard: no rm/DELETE against media or participant tables in ops scripts."""
    forbidden = ("rm -rf $MEDIA_DIR", 'rm -rf "$MEDIA_DIR"', "DELETE FROM reports", "DELETE FROM media", "DELETE FROM stamps")
    for script in (REPO / "scripts").glob("*.sh"):
        body = script.read_text()
        for needle in forbidden:
            assert needle not in body, f"{script.name} contains {needle!r}"
