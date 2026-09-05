"""Participant media on our own disk (DOMAIN §2, ARCHITECTURE §6.1).

A file is downloaded once, verified by its sha256 and never deleted — «removal» is
`hidden_at`. The download writes to `<name>.part` and renames it into place, so a crash
mid-download can never leave a half file that later looks complete.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.domain.types import ReportKind
from romantika.services.gateways import TelegramGateway

#: Extensions Telegram's mime types do not map to the way we want them to.
_SUFFIX_BY_MIME: dict[str, str] = {
    "image/jpeg": ".jpg",
    "audio/ogg": ".ogg",
    "video/mp4": ".mp4",
    "application/octet-stream": ".bin",
}

#: Fallback per kind when neither the mime type nor Telegram's path says anything.
_SUFFIX_BY_KIND: dict[ReportKind, str] = {
    ReportKind.PHOTO: ".jpg",
    ReportKind.VIDEO: ".mp4",
    ReportKind.VIDEO_NOTE: ".mp4",
    ReportKind.VOICE: ".ogg",
    ReportKind.AUDIO: ".mp3",
}

_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class MediaDTO:
    """A downloaded file: where it lies under the media root and what it hashes to."""

    media_id: uuid.UUID
    path: str
    sha256: str | None
    size: int | None


def suffix_for(*, kind: ReportKind, mime: str | None) -> str:
    """The extension a file of this kind and mime type gets on our disk."""
    if mime:
        known = _SUFFIX_BY_MIME.get(mime)
        if known:
            return known
        guessed = mimetypes.guess_extension(mime)
        if guessed:
            return guessed
    return _SUFFIX_BY_KIND.get(kind, ".bin")


def kind_for_mime(mime: str | None) -> ReportKind:
    """What a Mini App upload counts as (DOMAIN §2): images and videos are a maximum,
    audio a minimum, anything else a document (maximum), exactly like the bot's message kinds."""
    head = (mime or "").split("/", 1)[0].lower()
    if head == "image":
        return ReportKind.PHOTO
    if head == "video":
        return ReportKind.VIDEO
    if head == "audio":
        return ReportKind.AUDIO
    return ReportKind.DOCUMENT


def new_relative_path(*, season_slug: str, user_id: int, suffix: str) -> str:
    """`<season_slug>/<user_id>/<uuid>.<ext>` — unique, and readable in a backup listing."""
    return f"{season_slug}/{user_id}/{uuid.uuid4()}{suffix}"


class MediaStore:
    """The media directory (`MEDIA_DIR`). The only place that writes participant files."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def full_path(self, relative: str) -> Path:
        return self.root / relative

    async def download(
        self,
        session: AsyncSession,
        media_id: uuid.UUID,
        telegram: TelegramGateway,
        *,
        now: datetime,
    ) -> MediaDTO:
        """Fetch the file from Telegram unless we already have it. Idempotent by design.

        The bot calls this right after `reports.accept`; when it raises, the caller enqueues
        a `media_download` job and the worker calls it again with the same `media_id`.
        """
        row = await session.get(models.Media, media_id)
        if row is None:
            raise LookupError(f"media {media_id} does not exist")
        if row.downloaded_at is not None and self.full_path(row.path).exists():
            return MediaDTO(media_id=row.id, path=row.path, sha256=row.sha256, size=row.size)
        if row.tg_file_id is None:
            # Uploaded through the Mini App: the bytes came with the request, there is nothing
            # to fetch. Reaching here means the upload failed mid-way and the row is a stub.
            raise LookupError(f"media {media_id} was uploaded directly and has no Telegram file")

        remote = await telegram.get_file(row.tg_file_id)
        relative = _with_suffix(row.path, PurePosixPath(remote.file_path).suffix)
        target = self.full_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        await telegram.download_file(remote.file_path, part)

        digest, size = await asyncio.to_thread(_finalize, part, target, remote.file_size)
        row.path = relative
        row.sha256 = digest
        row.size = size
        row.downloaded_at = now
        await session.flush()
        return MediaDTO(media_id=row.id, path=relative, sha256=digest, size=size)

    async def save_upload(self, session: AsyncSession, media_id: uuid.UUID, part: Path, *, now: datetime) -> MediaDTO:
        """Move a file uploaded through the Mini App into place (ARCHITECTURE §8.1).

        `part` is a temporary file the web layer streamed the request body into; it must lie
        on the media filesystem (see `upload_part_path`) so the final rename is atomic. The
        row is marked downloaded only after the hash is taken, the same as for Telegram files.
        """
        row = await session.get(models.Media, media_id)
        if row is None:
            raise LookupError(f"media {media_id} does not exist")
        if row.downloaded_at is not None and self.full_path(row.path).exists():
            part.unlink(missing_ok=True)
            return MediaDTO(media_id=row.id, path=row.path, sha256=row.sha256, size=row.size)
        target = self.full_path(row.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, size = await asyncio.to_thread(_finalize, part, target, None)
        row.sha256 = digest
        row.size = size
        row.downloaded_at = now
        await session.flush()
        return MediaDTO(media_id=row.id, path=row.path, sha256=digest, size=size)

    async def receive_upload(
        self,
        session: AsyncSession,
        media_id: uuid.UUID,
        chunks: AsyncIterator[bytes],
        *,
        now: datetime,
        max_bytes: int,
    ) -> MediaDTO:
        """Stream a Mini App upload to disk and finalize it; nothing partial ever survives.

        Raises `UploadTooLargeError` past `max_bytes` (the part file is removed); the caller's
        transaction then rolls the report back too.
        """
        row = await session.get(models.Media, media_id)
        if row is None:
            raise LookupError(f"media {media_id} does not exist")
        part = self.upload_part_path(row.path)
        size = 0
        try:
            with part.open("wb") as handle:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadTooLargeError(f"{row.path}: more than {max_bytes} bytes")
                    handle.write(chunk)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return await self.save_upload(session, media_id, part, now=now)

    def upload_part_path(self, relative: str) -> Path:
        """Where the web layer streams an upload before `save_upload` renames it into place."""
        target = self.full_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.with_name(target.name + ".part")


def _with_suffix(relative: str, suffix: str) -> str:
    """Keep the generated uuid, take the extension Telegram actually served."""
    path = PurePosixPath(relative)
    if not suffix or path.suffix == suffix:
        return relative
    return str(path.with_suffix(suffix))


class UploadTooLargeError(ValueError):
    """A Mini App upload exceeded the size the web layer allows."""


class TruncatedDownloadError(OSError):
    """Telegram announced more bytes than arrived; the file on disk is incomplete."""


def _finalize(part: Path, target: Path, expected_size: int | None) -> tuple[str, int]:
    """Hash the downloaded part file and move it into place atomically (same filesystem).

    A stream cut short by the network must never become a `downloaded_at` row with a
    plausible sha256: the part file is dropped and the caller (or the `media_download` job)
    fetches it again.
    """
    digest = hashlib.sha256()
    size = 0
    with part.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    if expected_size is not None and size != expected_size:
        part.unlink(missing_ok=True)
        raise TruncatedDownloadError(f"{target.name}: expected {expected_size} bytes, got {size}")
    part.replace(target)
    return digest.hexdigest(), size


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """What the web layer needs to decide whether it may serve a file."""

    media_id: uuid.UUID
    path: str
    mime: str | None
    owner_id: int
    downloaded: bool
    hidden: bool


async def describe(session: AsyncSession, media_id: uuid.UUID) -> MediaInfo | None:
    row = await session.get(models.Media, media_id)
    if row is None:
        return None
    report = await session.get(models.Report, row.report_id)
    if report is None:
        return None
    return MediaInfo(
        media_id=row.id,
        path=row.path,
        mime=row.mime,
        owner_id=report.user_id,
        downloaded=row.downloaded_at is not None,
        hidden=row.hidden_at is not None or report.deleted_at is not None,
    )
