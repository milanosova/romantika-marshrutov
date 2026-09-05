"""Participant files: only the owner and the admin can see them."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from romantika.services import media as media_service
from romantika.web.deps import MediaStoreDep, PrincipalDep, SessionDep

router = APIRouter(tags=["media"])

#: Types a browser may render inline. Anything else — SVG, HTML, PDF, unknown — is served as
#: a download with a generic type, so a participant's file can never run as a page on our
#: origin (with the session cookie attached).
INLINE_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/heic",
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "audio/mpeg",
        "audio/ogg",
        "audio/mp4",
    }
)


@router.get("/media/{media_id}")
async def get_media(
    media_id: uuid.UUID, principal: PrincipalDep, session: SessionDep, media_store: MediaStoreDep
) -> FileResponse:
    info = await media_service.describe(session, media_id)
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")
    if info.owner_id != principal.user.id and not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your file")
    if info.hidden and not principal.is_admin:
        # Hidden, never deleted (DOMAIN §2): the owner took it out of the report, Mila still may look.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")
    path = media_store.full_path(info.path)
    if not info.downloaded or not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the file is not on the server yet")
    mime = (info.mime or "").split(";")[0].strip().lower()
    inline = mime in INLINE_TYPES
    headers = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    if not inline:
        headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
    return FileResponse(path, media_type=mime if inline else "application/octet-stream", headers=headers)
