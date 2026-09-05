"""Stage 2 acceptance: services layer (ARCHITECTURE §6, DOMAIN §2–§8).

READ-ONLY for implementers. These tests define the public service API. Every service takes an
`AsyncSession` first, plain values next, explicit `now`/`today` for time, and returns DTOs.
Services flush but never commit.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.domain.types import Level, ReportKind, StampLevel, WeekState
from romantika.services import (
    achievements,
    content,
    facts,
    freezes,
    jobs,
    journal,
    passport,
    people,
    reports,
    seed,
    stamps,
    summary,
    wishes,
    words,
)
from romantika.services.gateways import TelegramFile
from romantika.services.media import MediaStore
from romantika.services.people import TelegramUser
from romantika.services.reports import IncomingFile, IncomingMessage

SEASON_JSON = Path(__file__).resolve().parents[2] / "data" / "seasons" / "mexico-2026.json"
ADMIN_ID = 355363829
ALICE = 1001
BOB = 1002


def moscow(y: int, m: int, d: int, hour: int = 12) -> datetime:
    """An aware UTC datetime that is `hour` o'clock in Moscow on that day."""
    return datetime(y, m, d, hour, 0, tzinfo=UTC) - timedelta(hours=3)


@dataclass
class FakeTelegram:
    """TelegramGateway double: serves bytes for any file_id and counts downloads."""

    payload: bytes = b"fake-jpeg-bytes"
    calls: list[str] = field(default_factory=list)

    async def get_file(self, file_id: str) -> TelegramFile:
        return TelegramFile(file_path=f"photos/{file_id}.jpg", file_size=len(self.payload))

    async def download_file(self, file_path: str, destination: Path) -> None:
        self.calls.append(file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payload)


@pytest.fixture
async def season(db_session: AsyncSession) -> int:
    result = await seed.import_season(db_session, SEASON_JSON)
    await content.activate_season(db_session, result.season_id, actor_id=ADMIN_ID)
    for uid, name in ((ADMIN_ID, "Мила"), (ALICE, "Алиса"), (BOB, "Боб")):
        await people.upsert_user(db_session, TelegramUser(id=uid, username=None, first_name=name, last_name=None), now=moscow(2026, 8, 20))
        await people.ensure_member(db_session, result.season_id, uid, now=moscow(2026, 8, 20))
    return result.season_id


def text_message(text: str, msg_id: int = 1) -> IncomingMessage:
    return IncomingMessage(kind=ReportKind.TEXT, text=text, tg_chat_id=ALICE, tg_message_id=msg_id, files=[])


def photo_message(file_id: str = "AgACAgIAAxkBAAI", msg_id: int = 2, caption: str | None = None) -> IncomingMessage:
    return IncomingMessage(
        kind=ReportKind.PHOTO,
        text=caption,
        tg_chat_id=ALICE,
        tg_message_id=msg_id,
        files=[IncomingFile(kind=ReportKind.PHOTO, file_id=file_id, file_unique_id="u-" + file_id, mime="image/jpeg", size=None, width=1280, height=960)],
    )


# --- content -------------------------------------------------------------------


async def test_active_season_and_current_week(db_session: AsyncSession, season: int) -> None:
    active = await content.active_season(db_session, today=date(2026, 9, 3))
    assert active is not None and active.id == season and active.slug == "mexico-2026"
    week = await content.current_week(db_session, season, today=date(2026, 9, 3))
    assert week is not None and week.number == 1 and week.title == "За столом"
    assert await content.current_week(db_session, season, today=date(2026, 8, 25)) is None
    assert await content.current_week(db_session, season, today=date(2026, 11, 20)) is None


async def test_update_week_writes_audit(db_session: AsyncSession, season: int) -> None:
    week = await content.current_week(db_session, season, today=date(2026, 9, 3))
    assert week is not None
    updated = await content.update_week(db_session, actor_id=ADMIN_ID, week_id=week.id, changes={"task_min": "Новый минимум"})
    assert updated.task_min == "Новый минимум"
    audit_rows = (await db_session.execute(select(func.count()).select_from(models.AuditLog).where(models.AuditLog.entity == "week"))).scalar_one()
    assert audit_rows == 1


# --- people ----------------------------------------------------------------------


async def test_upsert_keeps_first_contact(db_session: AsyncSession, season: int) -> None:
    later = moscow(2026, 9, 10)
    user = await people.upsert_user(db_session, TelegramUser(id=ALICE, username="alice", first_name="Алиса", last_name="Х"), now=later)
    assert user.username == "alice"
    assert user.joined_at == moscow(2026, 8, 20)
    joined = await people.ensure_member(db_session, season, ALICE, now=later)
    assert joined == moscow(2026, 8, 20)


async def test_dialog_state_expires(db_session: AsyncSession, season: int) -> None:
    t0 = moscow(2026, 9, 3, 10)
    await people.set_dialog_state(db_session, ALICE, "word", now=t0)
    state = await people.get_dialog_state(db_session, ALICE, now=t0 + timedelta(hours=1))
    assert state is not None and state.state == "word"
    assert await people.get_dialog_state(db_session, ALICE, now=t0 + timedelta(hours=7)) is None
    await people.set_dialog_state(db_session, ALICE, "fact", payload={"week": 1}, now=t0)
    await people.clear_dialog_state(db_session, ALICE)
    assert await people.get_dialog_state(db_session, ALICE, now=t0) is None


# --- reports, stamps, freezes ----------------------------------------------------


async def test_text_report_gives_min_stamp(db_session: AsyncSession, season: int) -> None:
    result = await reports.accept(db_session, season_id=season, user_id=ALICE, message=text_message("сделала минимум"), now=moscow(2026, 9, 2))
    assert not result.out_of_week
    assert result.week_number == 1
    assert result.level is StampLevel.MIN
    assert result.stamp_level is StampLevel.MIN
    assert result.freeze_granted is False
    assert result.media_ids == []


async def test_photo_report_gives_max_and_first_max_freeze(db_session: AsyncSession, season: int) -> None:
    first = await reports.accept(db_session, season_id=season, user_id=ALICE, message=photo_message(), now=moscow(2026, 9, 2))
    assert first.level is StampLevel.MAX and first.stamp_level is StampLevel.MAX
    assert first.freeze_granted is True
    assert len(first.media_ids) == 1
    second = await reports.accept(db_session, season_id=season, user_id=ALICE, message=photo_message("BBB", msg_id=3), now=moscow(2026, 9, 9))
    assert second.week_number == 2 and second.freeze_granted is False
    assert await freezes.bonus_count(db_session, season, ALICE) == 1


async def test_max_is_not_downgraded_by_text_or_fix(db_session: AsyncSession, season: int) -> None:
    await reports.accept(db_session, season_id=season, user_id=ALICE, message=photo_message(), now=moscow(2026, 9, 2))
    after_text = await reports.accept(db_session, season_id=season, user_id=ALICE, message=text_message("и текст", msg_id=5), now=moscow(2026, 9, 3))
    assert after_text.level is StampLevel.MIN and after_text.stamp_level is StampLevel.MAX
    fix = await reports.fix_level(db_session, season_id=season, user_id=ALICE, week_number=1, level=StampLevel.MIN, now=moscow(2026, 9, 3))
    assert fix.ok is False and fix.stamp_level is StampLevel.MAX
    fix_up = await reports.fix_level(db_session, season_id=season, user_id=BOB, week_number=1, level=StampLevel.MAX, now=moscow(2026, 9, 3))
    assert fix_up.ok is False and fix_up.stamp_level is None, "no report — nothing to fix"


async def test_fix_level_upgrades_text_report(db_session: AsyncSession, season: int) -> None:
    await reports.accept(db_session, season_id=season, user_id=ALICE, message=text_message("сделала"), now=moscow(2026, 9, 2))
    fix = await reports.fix_level(db_session, season_id=season, user_id=ALICE, week_number=1, level=StampLevel.MAX, now=moscow(2026, 9, 3))
    assert fix.ok is True and fix.stamp_level is StampLevel.MAX


async def test_cancel_recomputes_stamp(db_session: AsyncSession, season: int) -> None:
    photo = await reports.accept(db_session, season_id=season, user_id=ALICE, message=photo_message(), now=moscow(2026, 9, 2))
    text = await reports.accept(db_session, season_id=season, user_id=ALICE, message=text_message("текст", msg_id=7), now=moscow(2026, 9, 3))
    cancelled = await reports.cancel(db_session, user_id=ALICE, report_id=photo.report_id, now=moscow(2026, 9, 3))
    assert cancelled.ok is True and cancelled.stamp_level is StampLevel.MIN
    cancelled = await reports.cancel(db_session, user_id=ALICE, report_id=text.report_id, now=moscow(2026, 9, 3))
    assert cancelled.ok is True and cancelled.stamp_level is None
    report_rows = (await db_session.execute(select(func.count()).select_from(models.Report).where(models.Report.user_id == ALICE))).scalar_one()
    assert report_rows == 2, "reports are never physically deleted"
    foreign = await reports.cancel(db_session, user_id=BOB, report_id=photo.report_id, now=moscow(2026, 9, 3))
    assert foreign.ok is False


async def test_out_of_week_message_is_stored(db_session: AsyncSession, season: int) -> None:
    result = await reports.accept(db_session, season_id=season, user_id=ALICE, message=text_message("привет до сезона"), now=moscow(2026, 8, 25))
    assert result.out_of_week is True and result.week_number is None and result.stamp_level is None
    row = await db_session.get(models.Report, result.report_id)
    assert row is not None and row.week_id is None and row.text == "привет до сезона"


async def test_admin_stamp_override(db_session: AsyncSession, season: int) -> None:
    level = await stamps.admin_set(db_session, actor_id=ADMIN_ID, season_id=season, user_id=BOB, week_number=1, level=StampLevel.MAX, now=moscow(2026, 9, 20))
    assert level is StampLevel.MAX
    view = await passport.build(db_session, season_id=season, user_id=BOB, today=date(2026, 9, 20))
    assert view.breakdown.states[1] is WeekState.STAMPED
    assert await stamps.admin_set(db_session, actor_id=ADMIN_ID, season_id=season, user_id=BOB, week_number=1, level=None, now=moscow(2026, 9, 20)) is None
    view = await passport.build(db_session, season_id=season, user_id=BOB, today=date(2026, 9, 20))
    assert view.breakdown.states[1] is not WeekState.STAMPED


async def test_freeze_grant_rules(db_session: AsyncSession, season: int) -> None:
    now = moscow(2026, 9, 3)
    assert await freezes.grant(db_session, season_id=season, user_id=ALICE, reason=models.FreezeReason.WORD, granted_by=None, now=now) is True
    assert await freezes.grant(db_session, season_id=season, user_id=ALICE, reason=models.FreezeReason.WORD, granted_by=None, now=now) is False
    assert await freezes.grant(db_session, season_id=season, user_id=ALICE, reason=models.FreezeReason.COMMENT, granted_by=ADMIN_ID, now=now) is True
    assert await freezes.grant(db_session, season_id=season, user_id=ALICE, reason=models.FreezeReason.MEETUP, granted_by=ADMIN_ID, now=now) is True
    assert await freezes.grant(db_session, season_id=season, user_id=ALICE, reason=models.FreezeReason.FRIEND, granted_by=ADMIN_ID, now=now) is False, "2 base + 3 bonus = cap 5"
    assert await freezes.bonus_count(db_session, season, ALICE) == 3


# --- media -----------------------------------------------------------------------


async def test_media_download_is_idempotent_and_hashed(db_session: AsyncSession, season: int, tmp_path: Path) -> None:
    store = MediaStore(tmp_path)
    telegram = FakeTelegram()
    result = await reports.accept(db_session, season_id=season, user_id=ALICE, message=photo_message(), now=moscow(2026, 9, 2))
    media_id = result.media_ids[0]
    dto = await store.download(db_session, media_id, telegram, now=moscow(2026, 9, 2))
    assert (tmp_path / dto.path).read_bytes() == telegram.payload
    assert dto.path.startswith(f"mexico-2026/{ALICE}/")
    assert dto.sha256 == hashlib.sha256(telegram.payload).hexdigest()
    assert dto.size == len(telegram.payload)
    again = await store.download(db_session, media_id, telegram, now=moscow(2026, 9, 3))
    assert again.path == dto.path and telegram.calls == [f"photos/{photo_message().files[0].file_id}.jpg"]
    row = await db_session.get(models.Media, media_id)
    assert row is not None and row.downloaded_at is not None and row.sha256 == dto.sha256


# --- achievements, words, facts, wishes -----------------------------------------


async def test_award_achievement_from_catalogue_and_free_text(db_session: AsyncSession, season: int) -> None:
    now = moscow(2026, 9, 3)
    first = await achievements.award(db_session, season_id=season, user_id=ALICE, code_or_text="повар", awarded_by=ADMIN_ID, now=now)
    assert first.created is True and first.code == "повар" and first.label == "🌮 Повар"
    again = await achievements.award(db_session, season_id=season, user_id=ALICE, code_or_text="повар", awarded_by=ADMIN_ID, now=now)
    assert again.created is False
    free = await achievements.award(db_session, season_id=season, user_id=ALICE, code_or_text="Самый ранний отчёт", awarded_by=ADMIN_ID, now=now)
    assert free.created is True and free.label == "Самый ранний отчёт"
    labels = await achievements.labels(db_session, season_id=season, user_id=ALICE)
    assert labels == ["🌮 Повар", "Самый ранний отчёт"]


async def test_words_parse_and_first_word_freeze(db_session: AsyncSession, season: int) -> None:
    now = moscow(2026, 9, 3)
    first = await words.add(db_session, season_id=season, user_id=ALICE, week_id=None, raw="sobremesa — время за столом уже после еды", now=now)
    assert (first.word, first.meaning) == ("sobremesa", "время за столом уже после еды")
    assert first.freeze_granted is True
    second = await words.add(db_session, season_id=season, user_id=ALICE, week_id=None, raw="antojo: внезапное желание", now=now)
    assert (second.word, second.meaning, second.freeze_granted) == ("antojo", "внезапное желание", False)
    third = await words.add(db_session, season_id=season, user_id=ALICE, week_id=None, raw="  fiesta  ", now=now)
    assert (third.word, third.meaning) == ("fiesta", "")
    view = await words.season_dictionary(db_session, season, today=date(2026, 9, 9))
    assert [w.word for w in view.week_words] == ["antojo", "alebrije"], "only weeks that have started"
    assert len(view.user_words) == 3


async def test_facts_add_list_remove(db_session: AsyncSession, season: int) -> None:
    now = moscow(2026, 9, 3)
    fact_id = await facts.add(db_session, season_id=season, week_id=None, text="Ацтеки называли себя мешика", author_id=None, now=now)
    await facts.add(db_session, season_id=season, week_id=None, text="Какао было валютой", author_id=ALICE, now=now)
    listed = await facts.list_active(db_session, season)
    assert [f.text for f in listed] == ["Ацтеки называли себя мешика", "Какао было валютой"]
    assert listed[0].author_id is None and listed[1].author_id == ALICE
    assert await facts.remove(db_session, fact_id=fact_id, actor_id=ADMIN_ID, now=now) is True
    assert [f.text for f in await facts.list_active(db_session, season)] == ["Какао было валютой"]
    assert await facts.remove(db_session, fact_id=fact_id, actor_id=ADMIN_ID, now=now) is False


async def test_wishes_upsert(db_session: AsyncSession, season: int) -> None:
    now = moscow(2026, 9, 3)
    assert await wishes.get_wish(db_session, season, ALICE) is None
    await wishes.set_wish(db_session, season_id=season, user_id=ALICE, text="Ты молодец", now=now)
    await wishes.set_wish(db_session, season_id=season, user_id=ALICE, text="Ты большая молодец", now=now)
    assert await wishes.get_wish(db_session, season, ALICE) == "Ты большая молодец"


# --- passport, journal, summary --------------------------------------------------


async def test_passport_and_journal_views(db_session: AsyncSession, season: int) -> None:
    await reports.accept(db_session, season_id=season, user_id=ALICE, message=photo_message(caption="тако удались"), now=moscow(2026, 9, 2))
    await reports.accept(db_session, season_id=season, user_id=ALICE, message=text_message("нарисовала алебрихе", msg_id=9), now=moscow(2026, 9, 9))
    await achievements.award(db_session, season_id=season, user_id=ALICE, code_or_text="повар", awarded_by=ADMIN_ID, now=moscow(2026, 9, 9))
    await wishes.set_wish(db_session, season_id=season, user_id=ALICE, text="Так держать", now=moscow(2026, 9, 9))

    view = await passport.build(db_session, season_id=season, user_id=ALICE, today=date(2026, 9, 23))
    assert view.breakdown.stamps == 2 and view.stamps_max == 1
    assert view.breakdown.states[3] is WeekState.FROZEN
    assert view.breakdown.freezes_total == 3, "2 base + 1 for the first max"
    assert view.level is Level.TOURIST
    assert view.achievements == ["🌮 Повар"]

    j = await journal.build(db_session, season_id=season, user_id=ALICE, today=date(2026, 9, 23))
    assert [(w.number, w.level) for w in j.weeks] == [(1, StampLevel.MAX), (2, StampLevel.MIN)]
    assert j.weeks[0].quote == "тако удались" and j.weeks[1].quote == "нарисовала алебрихе"
    assert len(j.media) == 1
    assert j.achievements == ["🌮 Повар"] and j.wish == "Так держать"


async def test_week_summary_and_reminder_recipients(db_session: AsyncSession, season: int) -> None:
    week1 = await content.current_week(db_session, season, today=date(2026, 9, 3))
    assert week1 is not None
    await people.set_intent(db_session, season_id=season, user_id=ALICE, week_id=week1.id, choice=models.IntentChoice.TAKE, now=moscow(2026, 9, 1))
    await people.set_intent(db_session, season_id=season, user_id=BOB, week_id=week1.id, choice=models.IntentChoice.TRY, now=moscow(2026, 9, 1))
    await people.set_intent(db_session, season_id=season, user_id=ADMIN_ID, week_id=week1.id, choice=models.IntentChoice.SKIP, now=moscow(2026, 9, 1))
    await reports.accept(db_session, season_id=season, user_id=ALICE, message=photo_message(), now=moscow(2026, 9, 2))

    recipients = await summary.reminder_recipients(db_session, season_id=season, week_number=1)
    assert recipients == [BOB]

    s = await summary.week(db_session, season_id=season, week_number=1, today=date(2026, 9, 5))
    assert s.members_total == 3 and s.reports_total == 1
    assert s.took == [ALICE, BOB] and s.submitted == {ALICE: StampLevel.MAX} and s.took_not_submitted == [BOB]


# --- jobs ------------------------------------------------------------------------


async def test_jobs_claim_finish_and_retry(db_session: AsyncSession) -> None:
    now = moscow(2026, 9, 3)
    job_id = await jobs.enqueue(db_session, "media_download", {"media_id": str(uuid.uuid4())}, now=now)
    claimed = await jobs.claim(db_session, now=now)
    assert claimed is not None and claimed.id == job_id and claimed.kind == "media_download"
    assert await jobs.claim(db_session, now=now) is None, "a running job is not claimed twice"
    await jobs.finish(db_session, job_id, error="boom", now=now)
    row = await db_session.get(models.Job, job_id)
    assert row is not None and row.status == "queued" and row.attempts == 1 and row.run_after > now
    assert await jobs.claim(db_session, now=now) is None, "backoff: not before run_after"
    later = row.run_after + timedelta(seconds=1)
    assert (await jobs.claim(db_session, now=later)) is not None
    await jobs.finish(db_session, job_id, error=None, now=later)
    row = await db_session.get(models.Job, job_id)
    assert row is not None and row.status == "done" and row.finished_at is not None
