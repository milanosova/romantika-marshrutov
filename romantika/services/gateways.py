"""Ports to the outside world (ARCHITECTURE §6.1).

Services never talk to Telegram directly: they receive a `TelegramGateway`, so the bot can
pass an aiogram adapter and the tests a fake. Later stages add `send_message` and
`send_document` to the protocol; a gateway only has to implement what its callers use.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TelegramFile:
    """Answer of `getFile`: where the file lives on Telegram's side and how big it is."""

    file_path: str
    file_size: int | None = None


@dataclass(frozen=True, slots=True)
class SentMedia:
    """What Telegram answered to a file we sent: the message and the id it now has there."""

    message_id: int
    file_id: str | None


class TelegramGateway(Protocol):
    """The part of the Telegram API the services layer needs."""

    async def get_file(self, file_id: str) -> TelegramFile: ...

    async def download_file(self, file_path: str, destination: Path) -> None:
        """Write the file to `destination`; the caller creates the parent directory."""
        ...

    async def send_message(self, chat_id: int, text: str) -> None:
        """Deliver an HTML text; raise when Telegram refused (blocked bot, unknown chat)."""
        ...

    async def send_document(self, chat_id: int, path: Path, caption: str | None = None) -> None:
        """Deliver a file from our disk (the PDF journal)."""
        ...

    async def send_text(self, chat_id: int, text: str) -> int:
        """Like `send_message`, but returns the id of the (last) message sent, so the worker can
        remember which message in Mila's chat belongs to which participant (`links`)."""
        ...

    async def send_file(self, chat_id: int, path: Path, *, mime: str | None, caption: str | None = None) -> SentMedia:
        """Deliver a participant's file from our disk as a photo, video, audio or document
        according to `mime`; used for Mini App uploads, which Telegram has never seen."""
        ...
