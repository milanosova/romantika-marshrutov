"""Stage 3 acceptance: the aiogram bot (ARCHITECTURE §7, DOMAIN §7–§8).

READ-ONLY for implementers. Updates are fed into the real Dispatcher; Telegram is replaced by a
recording session, the media download by a fake gateway, time by an injected clock.

Contract used here:
- `romantika.bot.app.create_dispatcher(settings, session_factory, media_store, *, telegram=None,
  clock=None) -> aiogram.Dispatcher` — `telegram` is a TelegramGateway used for media downloads
  (default: adapter over the aiogram Bot), `clock` returns an aware datetime (default: now).
- `romantika.bot.send.split_text(text, limit=4096) -> list[str]`.
- `romantika.bot.keyboards.normalize_button(text) -> str` and `button_action(text) -> str | None`
  ("task", "today", "passport", "words", "facts", "more", "help", "write", "admin").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    CopyMessage,
    DeleteWebhook,
    GetFile,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    File,
    Message,
    MessageId,
    PhotoSize,
    Update,
    User,
    Voice,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.bot.app import create_dispatcher
from romantika.bot.keyboards import button_action, normalize_button
from romantika.bot.send import split_text
from romantika.config import Settings
from romantika.db import models
from romantika.services import content, seed
from romantika.services.gateways import TelegramFile
from romantika.services.media import MediaStore

SEASON_JSON = Path(__file__).resolve().parents[2] / "data" / "seasons" / "mexico-2026.json"
ADMIN_ID = 355363829
ALICE = 1001
TOKEN = "123456:TEST-TOKEN"


def moscow(y: int, m: int, d: int, hour: int = 12) -> datetime:
    return datetime(y, m, d, hour, 0, tzinfo=UTC) - timedelta(hours=3)


class RecordingSession(BaseSession):
    """Answers Bot API calls locally and records every method for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self._next_id = 1000

    async def close(self) -> None:  # pragma: no cover - nothing to close
        return None

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        self.calls.append(method)
        self._next_id += 1
        if isinstance(method, (SendMessage, SendPhoto)):
            return Message(message_id=self._next_id, date=datetime.now(UTC), chat=Chat(id=method.chat_id, type="private"))
        if isinstance(method, CopyMessage):
            return MessageId(message_id=self._next_id)
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u-" + method.file_id, file_path=f"photos/{method.file_id}.jpg")
        if isinstance(method, (AnswerCallbackQuery, DeleteWebhook)):
            return True
        return True

    async def stream_content(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("stream_content must not be used in tests")

    def sent_texts(self, chat_id: int) -> list[str]:
        return [m.text for m in self.calls if isinstance(m, SendMessage) and m.chat_id == chat_id]


@dataclass
class FakeTelegram:
    payload: bytes = b"fake-jpeg-bytes"
    calls: list[str] = field(default_factory=list)

    async def get_file(self, file_id: str) -> TelegramFile:
        return TelegramFile(file_path=f"photos/{file_id}.jpg", file_size=len(self.payload))

    async def download_file(self, file_path: str, destination: Path) -> None:
        self.calls.append(file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payload)


@dataclass
class Harness:
    dp: Dispatcher
    bot: Bot
    session: RecordingSession
    telegram: FakeTelegram
    media_root: Path
    now: datetime
    _update_id: int = 0

    def _next(self) -> int:
        self._update_id += 1
        return self._update_id

    async def text(self, user_id: int, text: str, *, reply_to: Message | None = None) -> None:
        message = Message(
            message_id=self._next(),
            date=self.now,
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="Алиса" if user_id == ALICE else "Мила"),
            text=text,
            reply_to_message=reply_to,
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), message=message))

    async def photo(self, user_id: int, caption: str | None = None, file_id: str = "AgACAgIAAxkBAAI") -> None:
        message = Message(
            message_id=self._next(),
            date=self.now,
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="Алиса"),
            caption=caption,
            photo=[
                PhotoSize(file_id="small", file_unique_id="u-small", width=90, height=60),
                PhotoSize(file_id=file_id, file_unique_id="u-" + file_id, width=1280, height=960),
            ],
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), message=message))

    async def voice(self, user_id: int) -> None:
        message = Message(
            message_id=self._next(),
            date=self.now,
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="Алиса"),
            voice=Voice(file_id="VOICE1", file_unique_id="u-voice", duration=5),
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), message=message))

    async def callback(self, user_id: int, data: str) -> None:
        query = CallbackQuery(
            id=str(self._next()),
            from_user=User(id=user_id, is_bot=False, first_name="Алиса"),
            chat_instance="ci",
            data=data,
            message=Message(message_id=self._next(), date=self.now, chat=Chat(id=user_id, type="private")),
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), callback_query=query))


@pytest.fixture
async def harness(db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    result = await seed.import_season(db_session, SEASON_JSON)
    await content.activate_season(db_session, result.season_id, actor_id=ADMIN_ID)
    await db_session.flush()

    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("ADMIN_IDS", str(ADMIN_ID))
    monkeypatch.setenv("ADMIN_CHAT_ID", str(ADMIN_ID))
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unused/unused")
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://romantika.example.test")
    settings = Settings()

    # Sessions created by the bot join the test transaction (SAVEPOINT), so nothing leaks.
    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    telegram = FakeTelegram()
    now = moscow(2026, 9, 2, 15)
    dp = create_dispatcher(settings, factory, MediaStore(settings.media_dir), telegram=telegram, clock=lambda: now)
    session = RecordingSession()
    bot = Bot(TOKEN, session=session)
    return Harness(dp=dp, bot=bot, session=session, telegram=telegram, media_root=settings.media_dir, now=now)


# --- pure helpers -----------------------------------------------------------------


def test_split_text_respects_limit_and_keeps_content() -> None:
    paragraphs = ["строка " * 300 + str(i) for i in range(6)]
    text = "\n\n".join(paragraphs)
    parts = split_text(text, limit=4096)
    assert len(parts) >= 2
    assert all(0 < len(p) <= 4096 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")
    assert split_text("короткий", limit=4096) == ["короткий"]


@pytest.mark.parametrize(
    ("label", "action"),
    [
        ("📋 Задание", "task"),
        ("📋️ Задание", "task"),
        ("сегодня", "today"),
        ("🌤 Сегодня", "today"),
        ("📘 Паспорт", "passport"),
        ("🛡 Мой паспорт", "passport"),
        ("📖 Словарь", "words"),
        ("💡 Что узнали", "facts"),
        ("⋯ Ещё", "more"),
        ("❔ Помощь", "help"),
        ("✉️ Написать Миле", "write"),
        ("⚙️ Мила", "admin"),
        ("просто текст отчёта", None),
    ],
)
def test_button_action_ignores_emoji(label: str, action: str | None) -> None:
    assert button_action(label) == action
    assert normalize_button("📋️ Задание") == "задание"


# --- participant flows --------------------------------------------------------------


async def test_start_sends_greeting_with_keyboard(harness: Harness) -> None:
    await harness.text(ALICE, "/start")
    sent = [m for m in harness.session.calls if isinstance(m, SendMessage) and m.chat_id == ALICE]
    assert sent, "no reply to /start"
    assert "Романтика маршрутов" in sent[0].text
    assert sent[0].reply_markup is not None, "reply keyboard must be attached"


async def test_task_shows_current_week_with_intent_buttons(harness: Harness) -> None:
    await harness.text(ALICE, "📋 Задание")
    texts = harness.session.sent_texts(ALICE)
    assert texts and "Неделя 1" in texts[-1] and "За столом" in texts[-1]
    last = [m for m in harness.session.calls if isinstance(m, SendMessage) and m.chat_id == ALICE][-1]
    markup = last.reply_markup
    assert markup is not None
    labels = [b.text for row in markup.inline_keyboard for b in row]  # type: ignore[union-attr]
    assert any("Берусь" in x for x in labels) and any("Попробую" in x for x in labels) and any("мимо" in x for x in labels)


async def test_text_report_gets_min_stamp_and_admin_copy(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "Сделала минимум: сварила кофе де олья")
    texts = harness.session.sent_texts(ALICE)
    assert texts and "минимум" in texts[-1].lower()
    admin_texts = harness.session.sent_texts(ADMIN_ID)
    assert admin_texts and "Алиса" in admin_texts[-1] and "неделю 1" in admin_texts[-1].lower()
    stamps = (await db_session.execute(select(models.Stamp).where(models.Stamp.user_id == ALICE))).scalars().all()
    assert len(stamps) == 1 and stamps[0].level == "min"


async def test_photo_report_downloads_media_and_gives_max(harness: Harness, db_session: AsyncSession) -> None:
    await harness.photo(ALICE, caption="тако удались")
    texts = harness.session.sent_texts(ALICE)
    assert texts and "максимум" in texts[-1].lower()
    assert harness.telegram.calls == ["photos/AgACAgIAAxkBAAI.jpg"], "largest photo size is downloaded once"
    media = (await db_session.execute(select(models.Media))).scalars().all()
    assert len(media) == 1 and media[0].downloaded_at is not None
    assert (harness.media_root / media[0].path).exists()
    assert any(isinstance(m, CopyMessage) and m.chat_id == ADMIN_ID for m in harness.session.calls), "attachment copied to admin"
    stamp = (await db_session.execute(select(models.Stamp).where(models.Stamp.user_id == ALICE))).scalar_one()
    assert stamp.level == "max"


async def test_voice_is_a_min_report_and_sticker_is_not(harness: Harness, db_session: AsyncSession) -> None:
    await harness.voice(ALICE)
    texts = harness.session.sent_texts(ALICE)
    assert texts and "минимум" in texts[-1].lower()
    reports = (await db_session.execute(select(models.Report).where(models.Report.user_id == ALICE))).scalars().all()
    assert len(reports) == 1 and reports[0].kind == "voice"


async def test_fix_level_button_does_not_downgrade_max(harness: Harness, db_session: AsyncSession) -> None:
    await harness.photo(ALICE)
    await harness.callback(ALICE, "level:1:min")
    stamp = (await db_session.execute(select(models.Stamp).where(models.Stamp.user_id == ALICE))).scalar_one()
    assert stamp.level == "max"
    assert any(isinstance(m, AnswerCallbackQuery) for m in harness.session.calls), "the button must be answered"


async def test_out_of_week_message_is_stored_and_forwarded(db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = await seed.import_season(db_session, SEASON_JSON)
    await content.activate_season(db_session, result.season_id, actor_id=ADMIN_ID)
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("ADMIN_IDS", str(ADMIN_ID))
    monkeypatch.setenv("ADMIN_CHAT_ID", str(ADMIN_ID))
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unused/unused")
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://romantika.example.test")
    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    now = moscow(2026, 8, 25)
    dp = create_dispatcher(Settings(), factory, MediaStore(tmp_path), telegram=FakeTelegram(), clock=lambda: now)
    session = RecordingSession()
    h = Harness(dp=dp, bot=Bot(TOKEN, session=session), session=session, telegram=FakeTelegram(), media_root=tmp_path, now=now)
    await h.text(ALICE, "Привет, я до сезона")
    texts = session.sent_texts(ALICE)
    assert texts and "недел" in texts[-1].lower()
    assert session.sent_texts(ADMIN_ID), "the message must reach the admin"
    row = (await db_session.execute(select(models.Report).where(models.Report.user_id == ALICE))).scalar_one()
    assert row.week_id is None and row.text == "Привет, я до сезона"
    assert (await db_session.execute(select(func.count()).select_from(models.Stamp))).scalar_one() == 0


async def test_passport_and_today(harness: Harness) -> None:
    await harness.photo(ALICE)
    await harness.text(ALICE, "📘 Паспорт")
    passport_text = harness.session.sent_texts(ALICE)[-1]
    assert "Штампов" in passport_text and "1" in passport_text and "Заморозок" in passport_text
    await harness.text(ALICE, "🌤 Сегодня")
    today_text = harness.session.sent_texts(ALICE)[-1]
    assert "Акбаль" in today_text, "2026-09-02 is 2 Акбаль in the tzolkin"
    assert "antojo" in today_text


async def test_long_reply_is_split(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "/start")
    await harness.text(ALICE, "💡 Что узнали")
    before = len(harness.session.sent_texts(ALICE))
    for i in range(60):
        await harness.text(ADMIN_ID, "/fact " + f"Факт номер {i}: " + "очень длинный факт про Мексику " * 6)
    await harness.text(ALICE, "💡 Что узнали")
    after = harness.session.sent_texts(ALICE)[before:]
    assert len(after) >= 2, "60 long facts do not fit into one Telegram message"
    assert all(len(t) <= 4096 for t in after)


# --- admin flows -----------------------------------------------------------------------


async def test_admin_results_and_reply_routing(harness: Harness) -> None:
    await harness.photo(ALICE, caption="тако удались")
    admin_copy = [m for m in harness.session.calls if isinstance(m, SendMessage) and m.chat_id == ADMIN_ID][-1]
    await harness.text(ADMIN_ID, "/results")
    results = harness.session.sent_texts(ADMIN_ID)[-1]
    assert "Сдали" in results and "Алиса" in results

    # Mila replies to the forwarded report header; the bot routes the reply back to Alice.
    # The recording session numbers outgoing messages sequentially (1001 + index of the call).
    sent_index = harness.session.calls.index(admin_copy)
    header_tg_id = 1001 + sent_index
    forwarded = Message(message_id=header_tg_id, date=harness.now, chat=Chat(id=ADMIN_ID, type="private"), text=admin_copy.text)
    await harness.text(ADMIN_ID, "Отличные тако!", reply_to=forwarded)
    alice_texts = harness.session.sent_texts(ALICE)
    assert alice_texts and "Мила ответила" in alice_texts[-1] and "Отличные тако!" in alice_texts[-1]


async def test_admin_badge_and_freeze_commands(harness: Harness, db_session: AsyncSession) -> None:
    await harness.photo(ALICE)
    await harness.text(ADMIN_ID, "/badge @alice повар")  # unknown username → helpful error, no crash
    await harness.text(ADMIN_ID, "/badge Алиса повар")
    achievements = (await db_session.execute(select(models.Achievement).where(models.Achievement.user_id == ALICE))).scalars().all()
    assert [a.code for a in achievements] == ["повар"]
    assert harness.session.sent_texts(ALICE)[-1].lower().count("повар") >= 1, "participant is notified"


async def test_non_admin_cannot_use_admin_commands(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "/results")
    await harness.text(ALICE, "/badge Алиса повар")
    assert (await db_session.execute(select(func.count()).select_from(models.Achievement))).scalar_one() == 0
    texts = harness.session.sent_texts(ALICE)
    assert texts and "Сдали" not in texts[-1]
