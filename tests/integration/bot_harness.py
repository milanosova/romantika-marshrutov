"""Shared harness for the bot integration tests.

Grown out of the stage-3 acceptance scaffolding (`tests/acceptance/test_stage3_bot.py`), which stays
read-only: updates are fed into the real Dispatcher, Telegram is a recording session, media
downloads go through a fake gateway and time is injected and *movable* (weeks and the dialog TTL
are exercised by moving the clock, not by sleeping).
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
    Audio,
    CallbackQuery,
    Chat,
    Document,
    File,
    InlineKeyboardMarkup,
    Location,
    Message,
    MessageId,
    PhotoSize,
    Sticker,
    Update,
    User,
    Video,
    VideoNote,
    Voice,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.bot.app import create_dispatcher
from romantika.config import Settings
from romantika.services import content, seed
from romantika.services.gateways import TelegramFile
from romantika.services.media import MediaStore

SEASON_JSON = Path(__file__).resolve().parents[2] / "data" / "seasons" / "mexico-2026.json"
ADMIN_ID = 355363829
ALICE = 1001
BOB = 1002
TOKEN = "123456:TEST-TOKEN"

# Season 1 (Мексика): week 1 = 31.08–06.09, week 2 = 07.09–13.09, week 3 = 14.09–20.09.
WEEK1 = (2026, 9, 2)
WEEK2 = (2026, 9, 9)


def moscow(y: int, m: int, d: int, hour: int = 12) -> datetime:
    """An aware UTC instant that is `hour` o'clock in Moscow on the given day."""
    return datetime(y, m, d, hour, 0, tzinfo=UTC) - timedelta(hours=3)


class MovableClock:
    """The dispatcher's clock; tests move it to cross weeks and the dialog TTL."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class RecordingSession(BaseSession):
    """Answers Bot API calls locally and records every method for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.message_ids: dict[int, int] = {}  # index in `calls` -> message_id we handed back
        self._next_id = 1000

    async def close(self) -> None:  # pragma: no cover - nothing to close
        return None

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        index = len(self.calls)
        self.calls.append(method)
        self._next_id += 1
        self.message_ids[index] = self._next_id
        if isinstance(method, (SendMessage, SendPhoto)):
            return Message(
                message_id=self._next_id, date=datetime.now(UTC), chat=Chat(id=method.chat_id, type="private")
            )
        if isinstance(method, CopyMessage):
            return MessageId(message_id=self._next_id)
        if isinstance(method, GetFile):
            return File(
                file_id=method.file_id, file_unique_id="u-" + method.file_id, file_path=f"photos/{method.file_id}.jpg"
            )
        if isinstance(method, (AnswerCallbackQuery, DeleteWebhook)):
            return True
        return True

    async def stream_content(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("stream_content must not be used in tests")

    # --- assertions helpers ---------------------------------------------------------

    def messages(self, chat_id: int) -> list[SendMessage]:
        return [m for m in self.calls if isinstance(m, SendMessage) and m.chat_id == chat_id]

    def sent_texts(self, chat_id: int) -> list[str]:
        return [m.text for m in self.messages(chat_id)]

    def last_text(self, chat_id: int) -> str:
        texts = self.sent_texts(chat_id)
        assert texts, f"nothing was sent to {chat_id}"
        return texts[-1]

    def all_text(self, chat_id: int) -> str:
        return "\n".join(self.sent_texts(chat_id))

    def last_markup(self, chat_id: int) -> InlineKeyboardMarkup | None:
        messages = self.messages(chat_id)
        assert messages, f"nothing was sent to {chat_id}"
        markup = messages[-1].reply_markup
        return markup if isinstance(markup, InlineKeyboardMarkup) else None

    def buttons(self, chat_id: int) -> list[tuple[str, str | None]]:
        """(label, callback_data) of the inline keyboard attached to the last message."""
        markup = self.last_markup(chat_id)
        if markup is None:
            return []
        return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]

    def message_id_of(self, method: TelegramMethod[Any]) -> int:
        """The Telegram message_id this recording session handed back for `method`."""
        return self.message_ids[self.calls.index(method)]

    def alerts(self) -> list[str | None]:
        return [m.text for m in self.calls if isinstance(m, AnswerCallbackQuery)]

    def reset(self) -> None:
        self.calls.clear()
        self.message_ids.clear()


@dataclass
class FakeTelegram:
    """The media gateway; `broken=True` makes every download fail like a Telegram outage."""

    payload: bytes = b"fake-jpeg-bytes"
    broken: bool = False
    calls: list[str] = field(default_factory=list)
    sent_messages: list[tuple[int, str]] = field(default_factory=list)

    async def send_message(self, chat_id: int, text: str) -> None:
        """The panel's «Напомнить сейчас» hands this gateway to the reminder service."""
        self.sent_messages.append((chat_id, text))

    async def get_file(self, file_id: str) -> TelegramFile:
        if self.broken:
            raise RuntimeError("Telegram is down")
        return TelegramFile(file_path=f"photos/{file_id}.jpg", file_size=len(self.payload))

    async def download_file(self, file_path: str, destination: Path) -> None:
        if self.broken:
            raise RuntimeError("Telegram is down")
        self.calls.append(file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payload)


NAMES = {ALICE: "Алиса", BOB: "Боб", ADMIN_ID: "Мила"}


@dataclass
class Harness:
    dp: Dispatcher
    bot: Bot
    session: RecordingSession
    telegram: FakeTelegram
    media_root: Path
    clock: MovableClock
    _update_id: int = 0

    @property
    def now(self) -> datetime:
        return self.clock.now

    def set_now(self, value: datetime) -> None:
        self.clock.now = value

    def advance(self, delta: timedelta) -> None:
        self.clock.now += delta

    def _next(self) -> int:
        self._update_id += 1
        return self._update_id

    def _user(self, user_id: int) -> User:
        return User(
            id=user_id,
            is_bot=False,
            first_name=NAMES.get(user_id, f"User{user_id}"),
            username=None if user_id == ADMIN_ID else f"u{user_id}",
        )

    async def _feed(self, message: Message) -> None:
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), message=message))

    def _message(self, user_id: int, **kwargs: Any) -> Message:
        return Message(
            message_id=self._next(),
            date=self.now,
            chat=Chat(id=user_id, type="private"),
            from_user=self._user(user_id),
            **kwargs,
        )

    async def text(self, user_id: int, text: str, *, reply_to: Message | None = None) -> None:
        await self._feed(self._message(user_id, text=text, reply_to_message=reply_to))

    async def photo(self, user_id: int, caption: str | None = None, file_id: str = "AgACAgIAAxkBAAI") -> None:
        await self._feed(
            self._message(
                user_id,
                caption=caption,
                photo=[
                    PhotoSize(file_id="small", file_unique_id="u-small", width=90, height=60),
                    PhotoSize(file_id=file_id, file_unique_id="u-" + file_id, width=1280, height=960),
                ],
            )
        )

    async def video(self, user_id: int, caption: str | None = None) -> None:
        await self._feed(
            self._message(
                user_id,
                caption=caption,
                video=Video(
                    file_id="VID1", file_unique_id="u-vid", width=640, height=480, duration=10, mime_type="video/mp4"
                ),
            )
        )

    async def video_note(self, user_id: int) -> None:
        await self._feed(
            self._message(user_id, video_note=VideoNote(file_id="VN1", file_unique_id="u-vn", length=240, duration=7))
        )

    async def document(self, user_id: int, caption: str | None = None) -> None:
        await self._feed(
            self._message(
                user_id,
                caption=caption,
                document=Document(
                    file_id="DOC1", file_unique_id="u-doc", file_name="recipe.pdf", mime_type="application/pdf"
                ),
            )
        )

    async def voice(self, user_id: int) -> None:
        await self._feed(self._message(user_id, voice=Voice(file_id="VOICE1", file_unique_id="u-voice", duration=5)))

    async def audio(self, user_id: int) -> None:
        await self._feed(
            self._message(
                user_id, audio=Audio(file_id="AUD1", file_unique_id="u-aud", duration=120, mime_type="audio/mpeg")
            )
        )

    async def sticker(self, user_id: int) -> None:
        await self._feed(
            self._message(
                user_id,
                sticker=Sticker(
                    file_id="STK1",
                    file_unique_id="u-stk",
                    type="regular",
                    width=512,
                    height=512,
                    is_animated=False,
                    is_video=False,
                ),
            )
        )

    async def location(self, user_id: int) -> None:
        await self._feed(self._message(user_id, location=Location(latitude=19.43, longitude=-99.13)))

    async def callback(self, user_id: int, data: str) -> None:
        query = CallbackQuery(
            id=str(self._next()),
            from_user=self._user(user_id),
            chat_instance="ci",
            data=data,
            message=Message(message_id=self._next(), date=self.now, chat=Chat(id=user_id, type="private")),
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), callback_query=query))

    def admin_message(self, method: TelegramMethod[Any]) -> Message:
        """The message Mila sees in her chat, so a test can reply to it."""
        return Message(
            message_id=self.session.message_id_of(method),
            date=self.now,
            chat=Chat(id=ADMIN_ID, type="private"),
            text=getattr(method, "text", None),
        )


async def build_harness(
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    now: datetime | None = None,
    telegram: FakeTelegram | None = None,
) -> Harness:
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

    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    gateway = telegram if telegram is not None else FakeTelegram()
    clock = MovableClock(now or moscow(*WEEK1, 15))
    dp = create_dispatcher(settings, factory, MediaStore(settings.media_dir), telegram=gateway, clock=clock)
    session = RecordingSession()
    return Harness(
        dp=dp,
        bot=Bot(TOKEN, session=session),
        session=session,
        telegram=gateway,
        media_root=settings.media_dir,
        clock=clock,
    )
