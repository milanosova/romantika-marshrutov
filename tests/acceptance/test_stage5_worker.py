"""Stage 5 acceptance: worker (jobs, PDF journal, reminders, alerts) — ARCHITECTURE §9–§10.

READ-ONLY for implementers. Contract used here:
- `romantika.pdf.journal.render_journal_html(view) -> str` and `render_journal_pdf(view) -> bytes`
  where `view` is `romantika.services.journal.JournalView`.
- `romantika.worker.runner.run_once(session_factory, *, telegram, media_store, now) -> str | None`
  claims and executes one job (returns the job kind, or None when the queue is empty).
- `romantika.worker.schedulers.reminders_tick(session, *, telegram, now) -> int` — number of
  reminders sent this tick (0 when nothing is due or already sent today).
- `romantika.worker.schedulers.backup_status_tick(session, *, telegram, backups_dir, now) -> str | None`
  — returns the alert text sent to the admin, or None when backups are healthy.
- `TelegramGateway` (romantika.services.gateways) gains `send_message(chat_id, text)` and
  `send_document(chat_id, path, caption)`.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.db import models
from romantika.domain.types import ReportKind
from romantika.pdf.journal import render_journal_html, render_journal_pdf
from romantika.services import achievements, content, jobs, journal, people, reports, seed, wishes
from romantika.services.gateways import TelegramFile
from romantika.services.media import MediaStore
from romantika.services.people import TelegramUser
from romantika.services.reports import IncomingFile, IncomingMessage
from romantika.worker.runner import run_once
from romantika.worker.schedulers import backup_status_tick, reminders_tick

SEASON_JSON = Path(__file__).resolve().parents[2] / "data" / "seasons" / "mexico-2026.json"
ADMIN_ID = 355363829
ALICE = 1001
BOB = 1002


def moscow(y: int, m: int, d: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(y, m, d, hour, minute, tzinfo=UTC) - timedelta(hours=3)


@dataclass
class FakeTelegram:
    payload: bytes = b"fake-jpeg-bytes"
    calls: list[str] = field(default_factory=list)
    messages: list[tuple[int, str]] = field(default_factory=list)
    documents: list[tuple[int, Path, str | None]] = field(default_factory=list)

    async def get_file(self, file_id: str) -> TelegramFile:
        return TelegramFile(file_path=f"photos/{file_id}.jpg", file_size=len(self.payload))

    async def download_file(self, file_path: str, destination: Path) -> None:
        self.calls.append(file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payload)

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))

    async def send_document(self, chat_id: int, path: Path, caption: str | None = None) -> None:
        self.documents.append((chat_id, path, caption))


@pytest.fixture
async def world(db_session: AsyncSession, tmp_path: Path) -> dict[str, object]:
    result = await seed.import_season(db_session, SEASON_JSON)
    await content.activate_season(db_session, result.season_id, actor_id=ADMIN_ID)
    season = result.season_id
    for uid, name in ((ADMIN_ID, "Мила"), (ALICE, "Алиса"), (BOB, "Боб")):
        await people.upsert_user(db_session, TelegramUser(id=uid, username=None, first_name=name, last_name=None), now=moscow(2026, 8, 20))
        await people.ensure_member(db_session, season, uid, now=moscow(2026, 8, 20))
    store = MediaStore(tmp_path / "media")
    telegram = FakeTelegram()
    photo = IncomingMessage(
        kind=ReportKind.PHOTO,
        text="тако удались",
        tg_chat_id=ALICE,
        tg_message_id=1,
        files=[IncomingFile(kind=ReportKind.PHOTO, file_id="AAA", file_unique_id="u-AAA", mime="image/jpeg", size=None, width=1280, height=960)],
    )
    accepted = await reports.accept(db_session, season_id=season, user_id=ALICE, message=photo, now=moscow(2026, 9, 2))
    await store.download(db_session, accepted.media_ids[0], telegram, now=moscow(2026, 9, 2))
    await reports.accept(db_session, season_id=season, user_id=ALICE, message=IncomingMessage(kind=ReportKind.TEXT, text="нарисовала алебрихе", tg_chat_id=ALICE, tg_message_id=2, files=[]), now=moscow(2026, 9, 9))
    await achievements.award(db_session, season_id=season, user_id=ALICE, code_or_text="повар", awarded_by=ADMIN_ID, now=moscow(2026, 9, 9))
    await wishes.set_wish(db_session, season_id=season, user_id=ALICE, text="Так держать", now=moscow(2026, 9, 9))
    await db_session.flush()
    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    return {"season": season, "store": store, "telegram": telegram, "factory": factory, "tmp": tmp_path}


# --- PDF ------------------------------------------------------------------------------


async def test_journal_pdf_renders_with_cyrillic_and_photo(db_session: AsyncSession, world: dict[str, object]) -> None:
    view = await journal.build(db_session, season_id=world["season"], user_id=ALICE, today=datetime(2026, 11, 18, tzinfo=UTC).date())  # type: ignore[arg-type]
    html = render_journal_html(view)
    assert "Алиса" in html and "За столом" in html and "тако удались" in html and "🌮 Повар" in html and "Так держать" in html
    pdf = render_journal_pdf(view)
    assert pdf[:5] == b"%PDF-"
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) >= 1
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Алиса" in text and "За столом" in text, "Cyrillic must survive the PDF (fonts installed)"


# --- jobs -----------------------------------------------------------------------------


async def test_worker_runs_journal_pdf_job_and_sends_document(db_session: AsyncSession, world: dict[str, object]) -> None:
    telegram: FakeTelegram = world["telegram"]  # type: ignore[assignment]
    store: MediaStore = world["store"]  # type: ignore[assignment]
    factory = world["factory"]
    now = moscow(2026, 9, 10)
    job_id = await jobs.enqueue(db_session, "journal_pdf", {"user_id": ALICE, "season_id": world["season"], "chat_id": ALICE}, now=now)
    await db_session.flush()
    kind = await run_once(factory, telegram=telegram, media_store=store, now=now)  # type: ignore[arg-type]
    assert kind == "journal_pdf"
    assert len(telegram.documents) == 1
    chat_id, path, _caption = telegram.documents[0]
    assert chat_id == ALICE and path.exists() and path.read_bytes()[:5] == b"%PDF-"
    assert path.is_relative_to(store.root / "journals")
    job = await db_session.get(models.Job, job_id)
    assert job is not None and job.status == "done"
    assert await run_once(factory, telegram=telegram, media_store=store, now=now) is None  # type: ignore[arg-type]


async def test_worker_retries_media_download(db_session: AsyncSession, world: dict[str, object]) -> None:
    store: MediaStore = world["store"]  # type: ignore[assignment]
    factory = world["factory"]
    now = moscow(2026, 9, 10)
    message = IncomingMessage(
        kind=ReportKind.PHOTO,
        text=None,
        tg_chat_id=BOB,
        tg_message_id=3,
        files=[IncomingFile(kind=ReportKind.PHOTO, file_id="BBB", file_unique_id="u-BBB", mime="image/jpeg", size=None, width=100, height=100)],
    )
    accepted = await reports.accept(db_session, season_id=world["season"], user_id=BOB, message=message, now=now)  # type: ignore[arg-type]
    media_id = accepted.media_ids[0]
    await jobs.enqueue(db_session, "media_download", {"media_id": str(media_id)}, now=now)
    await db_session.flush()

    class FailingTelegram(FakeTelegram):
        async def download_file(self, file_path: str, destination: Path) -> None:
            raise RuntimeError("telegram down")

    assert await run_once(factory, telegram=FailingTelegram(), media_store=store, now=now) == "media_download"  # type: ignore[arg-type]
    job = (await db_session.execute(select(models.Job).where(models.Job.kind == "media_download"))).scalar_one()
    assert job.status == "queued" and job.attempts == 1 and "telegram down" in (job.error or "")
    later = job.run_after + timedelta(seconds=1)
    assert await run_once(factory, telegram=FakeTelegram(), media_store=store, now=later) == "media_download"  # type: ignore[arg-type]
    row = await db_session.get(models.Media, media_id)
    assert row is not None and row.downloaded_at is not None and (store.root / row.path).exists()


# --- schedulers -------------------------------------------------------------------------


async def test_reminders_go_out_once_per_slot(db_session: AsyncSession, world: dict[str, object]) -> None:
    telegram: FakeTelegram = world["telegram"]  # type: ignore[assignment]
    season: int = world["season"]  # type: ignore[assignment]
    week = await content.current_week(db_session, season, today=datetime(2026, 9, 3, tzinfo=UTC).date())
    assert week is not None
    await people.set_intent(db_session, season_id=season, user_id=BOB, week_id=week.id, choice=models.IntentChoice.TAKE, now=moscow(2026, 9, 1))
    await people.set_intent(db_session, season_id=season, user_id=ALICE, week_id=week.id, choice=models.IntentChoice.TAKE, now=moscow(2026, 9, 1))

    assert await reminders_tick(db_session, telegram=telegram, now=moscow(2026, 9, 3, 18, 59), admin_chat=ADMIN_ID) == 0, "Thursday before 19:00"
    sent = await reminders_tick(db_session, telegram=telegram, now=moscow(2026, 9, 3, 19, 0), admin_chat=ADMIN_ID)
    to_people = [(chat, text) for chat, text in telegram.messages if chat != ADMIN_ID]
    assert sent == 1 and to_people[-1][0] == BOB, "Alice already has a stamp"
    assert "За столом" in to_people[-1][1]
    assert await reminders_tick(db_session, telegram=telegram, now=moscow(2026, 9, 3, 21, 0), admin_chat=ADMIN_ID) == 0, "sent once per day"
    assert await reminders_tick(db_session, telegram=telegram, now=moscow(2026, 9, 6, 12, 5), admin_chat=ADMIN_ID) == 1, "Sunday noon"
    to_people = [(chat, text) for chat, text in telegram.messages if chat != ADMIN_ID]
    assert "18:00" in to_people[-1][1]
    assert await reminders_tick(db_session, telegram=telegram, now=moscow(2026, 9, 5, 12, 5), admin_chat=ADMIN_ID) == 0, "Saturday: nothing"
    admin_notes = [t for chat, t in telegram.messages if chat == ADMIN_ID]
    assert admin_notes, "admin gets a report of each reminder run"


async def test_reminders_respect_switch(db_session: AsyncSession, world: dict[str, object]) -> None:
    telegram: FakeTelegram = world["telegram"]  # type: ignore[assignment]
    season: int = world["season"]  # type: ignore[assignment]
    week = await content.current_week(db_session, season, today=datetime(2026, 9, 3, tzinfo=UTC).date())
    assert week is not None
    await people.set_intent(db_session, season_id=season, user_id=BOB, week_id=week.id, choice=models.IntentChoice.TRY, now=moscow(2026, 9, 1))
    await content.set_setting(db_session, "reminders_enabled", "off")
    assert await reminders_tick(db_session, telegram=telegram, now=moscow(2026, 9, 3, 19, 30), admin_chat=ADMIN_ID) == 0


async def test_backup_status_alerts_when_stale_or_failed(db_session: AsyncSession, world: dict[str, object]) -> None:
    telegram: FakeTelegram = world["telegram"]  # type: ignore[assignment]
    backups: Path = world["tmp"] / "backups"  # type: ignore[operator]
    backups.mkdir()
    now = moscow(2026, 9, 10, 9)
    alert = await backup_status_tick(db_session, telegram=telegram, backups_dir=backups, now=now, admin_chat=ADMIN_ID)
    assert alert is not None and telegram.messages[-1][0] == ADMIN_ID, "no verification file at all → alert"

    (backups / "last-verify.json").write_text(json.dumps({"ok": True, "checked_at": (now - timedelta(days=2)).isoformat(), "dump": "romantika-2026-09-08.dump"}))
    assert await backup_status_tick(db_session, telegram=telegram, backups_dir=backups, now=now, admin_chat=ADMIN_ID) is None

    (backups / "last-verify.json").write_text(json.dumps({"ok": False, "checked_at": now.isoformat(), "errors": ["row count mismatch: stamps 10 != 9"]}))
    alert = await backup_status_tick(db_session, telegram=telegram, backups_dir=backups, now=now, admin_chat=ADMIN_ID)
    assert alert is not None and "stamps" in alert

    (backups / "last-verify.json").write_text(json.dumps({"ok": True, "checked_at": (now - timedelta(days=9)).isoformat()}))
    assert await backup_status_tick(db_session, telegram=telegram, backups_dir=backups, now=now, admin_chat=ADMIN_ID) is not None, "stale > 8 days"
