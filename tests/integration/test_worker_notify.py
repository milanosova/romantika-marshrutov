"""Worker delivery of what the Mini App queues (ARCHITECTURE §9.1): `telegram_notify`,
`reminders_now`; the Telegram file id written back on the first send."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.db import models
from romantika.domain.types import ReportKind, StampLevel
from romantika.services import content, notify, people, reports, seed
from romantika.services.gateways import SentMedia, TelegramFile
from romantika.services.media import MediaStore
from romantika.services.people import TelegramUser
from romantika.services.reports import IncomingFile, IncomingMessage
from romantika.worker.runner import run_once

SEASON_JSON = Path(__file__).resolve().parents[2] / "data" / "seasons" / "mexico-2026.json"
ADMIN_ID = 355363829
ALICE = 1001


def moscow(y: int, m: int, d: int, hour: int = 12) -> datetime:
    return datetime(y, m, d, hour, 0, tzinfo=UTC) - timedelta(hours=3)


@dataclass
class FakeGateway:
    texts: list[tuple[int, str]] = field(default_factory=list)
    files: list[tuple[int, Path, str | None]] = field(default_factory=list)
    next_id: int = 100

    async def get_file(self, file_id: str) -> TelegramFile:
        raise AssertionError("nothing to download in these tests")

    async def download_file(self, file_path: str, destination: Path) -> None:
        raise AssertionError("nothing to download in these tests")

    async def send_message(self, chat_id: int, text: str) -> None:
        self.texts.append((chat_id, text))

    async def send_document(self, chat_id: int, path: Path, caption: str | None = None) -> None:
        self.files.append((chat_id, path, caption))

    async def send_text(self, chat_id: int, text: str) -> int:
        self.texts.append((chat_id, text))
        self.next_id += 1
        return self.next_id

    async def send_file(self, chat_id: int, path: Path, *, mime: str | None, caption: str | None = None) -> SentMedia:
        assert path.exists(), path
        self.files.append((chat_id, path, mime))
        self.next_id += 1
        return SentMedia(message_id=self.next_id, file_id=f"TG-{path.name}")


async def _season(session: AsyncSession) -> int:
    result = await seed.import_season(session, SEASON_JSON)
    await content.activate_season(session, result.season_id, actor_id=ADMIN_ID)
    for uid, name in ((ADMIN_ID, "Мила"), (ALICE, "Алиса")):
        await people.upsert_user(session, TelegramUser(id=uid, first_name=name), now=moscow(2026, 8, 20))
        await people.ensure_member(session, result.season_id, uid, now=moscow(2026, 8, 20))
    return result.season_id


async def test_telegram_notify_sends_text_and_uploaded_file_and_remembers_links(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    now = moscow(2026, 9, 2, 15)
    season_id = await _season(db_session)
    store = MediaStore(tmp_path / "media")
    incoming = IncomingMessage(
        kind=ReportKind.PHOTO, text="тако", files=[IncomingFile(kind=ReportKind.PHOTO, file_id=None, mime="image/jpeg")]
    )
    accepted = await reports.accept(db_session, season_id=season_id, user_id=ALICE, message=incoming, now=now)
    (media_id,) = accepted.media_ids

    async def chunks():  # type: ignore[no-untyped-def]
        yield b"jpeg-"
        yield b"bytes"

    await store.receive_upload(db_session, media_id, chunks(), now=now, max_bytes=1000)
    week = await content.week_by_number(db_session, season_id, 1)
    assert week is not None
    await notify.enqueue_message(
        db_session,
        chat_id=ADMIN_ID,
        text="📨 Отчёт за неделю 1 от Алиса",
        media_ids=[media_id],
        link_user_id=ALICE,
        link_report_id=accepted.report_id,
        link_week_id=week.id,
        now=now,
    )
    await notify.enqueue_message(db_session, chat_id=ALICE, text="✅ Записала", now=now)
    await db_session.flush()

    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    gateway = FakeGateway()
    assert await run_once(factory, telegram=gateway, media_store=store, now=now) == "telegram_notify"
    assert await run_once(factory, telegram=gateway, media_store=store, now=now) == "telegram_notify"
    assert await run_once(factory, telegram=gateway, media_store=store, now=now) is None

    assert gateway.texts == [(ADMIN_ID, "📨 Отчёт за неделю 1 от Алиса"), (ALICE, "✅ Записала")]
    assert len(gateway.files) == 1 and gateway.files[0][0] == ADMIN_ID and gateway.files[0][2] == "image/jpeg"
    media = await db_session.get(models.Media, media_id)
    assert media is not None and media.tg_file_id == "TG-" + gateway.files[0][1].name, "file id written back"
    links = list(
        (await db_session.execute(select(models.AdminLink).order_by(models.AdminLink.admin_message_id))).scalars()
    )
    assert [(row.admin_message_id, row.user_id, row.report_id, row.week_id) for row in links] == [
        (101, ALICE, accepted.report_id, week.id),
        (102, ALICE, accepted.report_id, week.id),
    ], "both the header and the photo answer to a reply"
    done = list((await db_session.execute(select(models.Job).order_by(models.Job.id))).scalars())
    assert [j.status for j in done] == ["done", "done"]
    assert done[0].payload["sent"] == 2 and done[0].payload["skipped"] == 0, "result merged into the payload"


async def test_telegram_notify_skips_media_not_on_disk(db_session: AsyncSession, tmp_path: Path) -> None:
    now = moscow(2026, 9, 2, 15)
    season_id = await _season(db_session)
    store = MediaStore(tmp_path / "media")
    incoming = IncomingMessage(
        kind=ReportKind.PHOTO, files=[IncomingFile(kind=ReportKind.PHOTO, file_id="AAA", mime="image/jpeg")]
    )
    accepted = await reports.accept(db_session, season_id=season_id, user_id=ALICE, message=incoming, now=now)
    await notify.enqueue_message(db_session, chat_id=ADMIN_ID, text="шапка", media_ids=accepted.media_ids, now=now)
    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    gateway = FakeGateway()
    await run_once(factory, telegram=gateway, media_store=store, now=now)
    assert gateway.texts == [(ADMIN_ID, "шапка")] and gateway.files == []
    job = (await db_session.execute(select(models.Job))).scalar_one()
    assert job.status == "done" and job.payload["sent"] == 1 and job.payload["skipped"] == 1


async def test_reminders_now_reports_back_to_mila(db_session: AsyncSession, tmp_path: Path) -> None:
    now = moscow(2026, 9, 2, 15)
    season_id = await _season(db_session)
    week = await content.week_by_number(db_session, season_id, 1)
    assert week is not None
    await people.set_intent(
        db_session, season_id=season_id, user_id=ALICE, week_id=week.id, choice=models.IntentChoice.TAKE, now=now
    )
    await notify.enqueue_reminders_now(db_session, season_id=season_id, requested_by=ADMIN_ID, now=now)
    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    gateway = FakeGateway()
    assert await run_once(factory, telegram=gateway, media_store=MediaStore(tmp_path), now=now) == "reminders_now"
    assert gateway.texts[0][0] == ALICE and "выходные" in gateway.texts[0][1]
    assert gateway.texts[1] == (ADMIN_ID, "Напоминание ушло: 1 из 1")


async def test_season_end_queues_one_journal_per_stamped_participant(db_session: AsyncSession, tmp_path: Path) -> None:
    from romantika.services import stamps
    from romantika.worker.schedulers import season_end_tick

    season_id = await _season(db_session)
    week = await content.week_by_number(db_session, season_id, 1)
    assert week is not None
    await stamps.merge(
        db_session,
        season_id=season_id,
        user_id=ALICE,
        week_id=week.id,
        week_title=week.title,
        level=StampLevel.MIN,
        now=moscow(2026, 9, 2),
    )
    # the last day of the season (Wednesday 18.11) and the morning after: nothing yet
    assert await season_end_tick(db_session, now=moscow(2026, 11, 18, 23), admin_chat=ADMIN_ID) is False
    assert await season_end_tick(db_session, now=moscow(2026, 11, 19, 9), admin_chat=ADMIN_ID) is False
    # noon the day after: once
    assert await season_end_tick(db_session, now=moscow(2026, 11, 19, 12), admin_chat=ADMIN_ID) is True
    assert await season_end_tick(db_session, now=moscow(2026, 11, 19, 13), admin_chat=ADMIN_ID) is False
    assert await season_end_tick(db_session, now=moscow(2026, 11, 20, 12), admin_chat=ADMIN_ID) is False

    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    gateway = FakeGateway()
    now = moscow(2026, 11, 19, 12)
    assert await run_once(factory, telegram=gateway, media_store=MediaStore(tmp_path), now=now) == "season_journals"
    assert gateway.texts == [], "Mila's note is queued, not sent from inside the transaction"
    notes = list((await db_session.execute(select(models.Job).where(models.Job.kind == "telegram_notify"))).scalars())
    assert len(notes) == 1 and notes[0].payload["chat_id"] == ADMIN_ID
    assert "закончился — собираю журналы: 1 человек" in notes[0].payload["text"]
    queued = list((await db_session.execute(select(models.Job).where(models.Job.kind == "journal_pdf"))).scalars())
    assert [(j.payload["user_id"], j.payload["chat_id"], j.payload["requested_via"]) for j in queued] == [
        (ALICE, ALICE, "season_end")
    ], "Mila has no stamp, so she gets no journal; Alice gets hers"
