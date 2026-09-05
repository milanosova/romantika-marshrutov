"""Participant API (ARCHITECTURE §8.1): everything the bot does, for the Mini App.

The rules live in the services; this module only maps HTTP to them and picks the same
Russian texts the bot answers with (`romantika.texts.ru`). Anything that has to reach
Telegram — Mila's copy of a report, the participant's receipt — is queued for the worker
through `services.notify`, never sent from here.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy import text as sql_text
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from romantika.db import models
from romantika.domain.types import ReportKind, StampLevel
from romantika.services import content, facts, jobs, letters, notify, people, reports, stamps, words
from romantika.services import media as media_service
from romantika.services.people import TelegramUser
from romantika.services.reports import IncomingFile, IncomingMessage
from romantika.texts import ru
from romantika.web import auth, schemas, views
from romantika.web.deps import (
    MediaStoreDep,
    NowDep,
    Principal,
    PrincipalDep,
    SeasonDep,
    SessionDep,
    SettingsDep,
    TodayDep,
)

router = APIRouter(prefix="/api", tags=["api"])

#: Upload limits of one report from the Mini App. Telegram's own bot limit is 50 MB per file,
#: which is also what the worker can forward to Mila; ten files is an album.
MAX_UPLOAD_FILES = 10
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
#: One request altogether (`Content-Length`), checked before the body is read. Videos are
#: the only thing that gets near it; an album of photos is a few megabytes.
MAX_REQUEST_BYTES = 200 * 1024 * 1024
#: A report's text; the bot's own limit is Telegram's message length, the same order of size.
MAX_TEXT_CHARS = 4000
#: `client_id` / `edit_key` are opaque ids the app makes up (UUIDs); longer means a bug, not a cut.
MAX_KEY_CHARS = 64
#: The form parser's own ceiling on file parts — above ours so that our 413 speaks first.
MAX_FORM_FILES = 40
_CHUNK = 1024 * 1024


async def _multipart(request: Request) -> tuple[dict[str, str], dict[str, list[str]], list[UploadFile]]:
    """Read the multipart body of a report **after** the caller is authenticated.

    FastAPI parses declared `Form()`/`File()` parameters before it resolves dependencies,
    which would let anyone without initData fill the disk with spooled uploads and only then
    get a 401. Reading the form here, from the handler, keeps the order right; the size is
    checked from the header first, so an oversized body is refused before a byte of it lands.
    Returns the single-valued fields, the multi-valued ones and the files, in body order.
    """
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_REQUEST_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"вместе больше {MAX_REQUEST_BYTES // (1024 * 1024)} МБ")
    try:
        # The parser's own ceilings sit above ours, so the answers a client can hit are ours
        # (413 «не больше 10 файлов»); what the parser itself refuses is answered in Russian too.
        form = await request.form(max_files=MAX_FORM_FILES, max_fields=64)
    except (MultiPartException, StarletteHTTPException) as exc:
        detail = getattr(exc, "detail", None) or getattr(exc, "message", "")
        if "Too many files" in str(detail):
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, f"не больше {MAX_UPLOAD_FILES} файлов за раз"
            ) from exc
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "не смогла прочитать отправленное") from exc
    fields: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    files: list[UploadFile] = []
    for key, value in form.multi_items():
        if isinstance(value, UploadFile):
            if key != "files":
                continue  # a file under another name is not an attachment
            if not value.size:
                # An empty file is no file: it must neither earn a maximum nor vanish silently.
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, f"файл «{value.filename or ''}» пустой — не прикрепила"
                )
            files.append(value)
        else:
            if "\x00" in value:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "в тексте недопустимые символы")
            fields.setdefault(key, value)
            lists.setdefault(key, []).append(value)
    return fields, lists, files


def _attempt_key(fields: dict[str, str], name: str) -> str | None:
    """`client_id` / `edit_key`: one per attempt, at most 64 characters — never cut, or two
    different attempts would collapse into one report."""
    value = fields.get(name, "").strip()
    if len(value) > MAX_KEY_CHARS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{name} длиннее {MAX_KEY_CHARS} знаков")
    return value or None


async def _serialise(session: SessionDep, key: str) -> None:
    """Two retries of one attempt wait for each other on a transaction lock, so the second one
    finds the first one's row instead of doing the work twice."""
    await session.execute(sql_text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})


@router.get("/me", response_model=schemas.Me)
async def me(principal: PrincipalDep) -> schemas.Me:
    user = principal.user
    return schemas.Me(id=user.id, first_name=user.first_name, username=user.username, is_admin=principal.is_admin)


@router.post("/session", response_model=schemas.Me)
async def open_session(
    body: schemas.SessionIn,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    now: NowDep,
) -> schemas.Me:
    """Validate initData once and set the cookie that lets `<img src=/media/…>` load."""
    info = auth.validate_init_data(body.init_data, settings.bot_token, now=now)
    if info is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid init data")
    user = await people.upsert_user(
        session,
        TelegramUser(id=info.id, username=info.username, first_name=info.first_name, last_name=info.last_name),
        now=now,
    )
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.make_session_token(settings.bot_token, user.id, now=now),
        max_age=int(auth.SESSION_MAX_AGE.total_seconds()),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return schemas.Me(
        id=user.id,
        first_name=user.first_name,
        username=user.username,
        is_admin=user.is_admin or settings.is_admin(user.id),
    )


# --- home: task, today, passport ---------------------------------------------------


@router.get("/home", response_model=schemas.HomeOut)
async def home(
    principal: PrincipalDep, session: SessionDep, season: SeasonDep, today: TodayDep, settings: SettingsDep
) -> schemas.HomeOut:
    await people.ensure_member(session, season.id, principal.user.id, now=principal.user.joined_at)
    return await views.home_out(
        session,
        season=season,
        user_id=principal.user.id,
        today=today,
        principal_admin=principal.is_admin,
        settings=settings,
    )


@router.post("/intent", response_model=schemas.IntentOut)
async def set_intent(
    body: schemas.IntentIn,
    principal: PrincipalDep,
    session: SessionDep,
    season: SeasonDep,
    settings: SettingsDep,
    now: NowDep,
    today: TodayDep,
) -> schemas.IntentOut:
    """«Берусь · Попробую · В этот раз мимо» — the same row Mila's summary and the reminders read.

    Only for a week that has started: a future week is not shown to participants (DOMAIN §1),
    so an intent on it would be a guess about a task nobody has seen.
    """
    week = await content.week_by_number(session, season.id, body.week_number)
    if week is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such week")
    if week.starts_on > today:
        raise HTTPException(status.HTTP_409_CONFLICT, "эта неделя ещё не открылась")
    await people.set_intent(
        session,
        season_id=season.id,
        user_id=principal.user.id,
        week_id=week.id,
        choice=models.IntentChoice(body.choice),
        now=now,
    )
    await _notify_admin(
        session,
        settings,
        principal,
        f"👤 {ru.escape(principal.user.display_name_with_username)} — "
        f"<b>{ru.INTENT_NAMES[body.choice]}</b> на неделе {week.number}",
        now=now,
    )
    return schemas.IntentOut(choice=body.choice, hint=ru.INTENT_HINTS[body.choice])


# --- reports -----------------------------------------------------------------------


async def _chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(_CHUNK):
        yield chunk


@router.post("/reports", response_model=schemas.ReportResult, status_code=status.HTTP_201_CREATED)
async def submit_report(
    principal: PrincipalDep,
    session: SessionDep,
    season: SeasonDep,
    settings: SettingsDep,
    media_store: MediaStoreDep,
    now: NowDep,
    response: Response,
    request: Request,
) -> schemas.ReportResult:
    """A report from the Mini App: multipart `text`, `client_id` and `files`, judged by the
    bot's rules (DOMAIN §2).

    Files are streamed straight onto the media disk and hashed before the row is marked
    downloaded — the same guarantee as for files fetched from Telegram. Mila gets the usual
    copy with the header she can reply to, the participant the usual receipt in the chat.
    `client_id` makes a retry harmless: the same id answers with the report already made,
    and two retries racing each other are serialised on a transaction lock, so the second
    one finds the first one's row instead of tripping over the unique index.
    """
    fields, _, uploads = await _multipart(request)
    body = fields.get("text", "").strip()
    client_id = _attempt_key(fields, "client_id")
    if not body and not uploads:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "пустой отчёт: нужен текст или файл")
    if len(body) > MAX_TEXT_CHARS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"текст длиннее {MAX_TEXT_CHARS} знаков")
    if len(uploads) > MAX_UPLOAD_FILES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"не больше {MAX_UPLOAD_FILES} файлов за раз")
    if client_id:
        await _serialise(session, f"{principal.user.id}:{client_id}")
        existing = await reports.find_by_client_id(session, user_id=principal.user.id, client_id=client_id)
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return await _already_submitted(session, season, existing)

    incoming_files = [_incoming_file(upload) for upload in uploads]
    kind = incoming_files[0].kind if incoming_files else ReportKind.TEXT
    incoming = IncomingMessage(kind=kind, text=body or None, files=incoming_files, client_id=client_id or None)
    result = await reports.accept(session, season_id=season.id, user_id=principal.user.id, message=incoming, now=now)
    await _store_uploads(session, media_store, result.media_ids, uploads, now)

    author = principal.user.display_name_with_username
    if result.out_of_week or result.week_number is None:
        message = ru.OUT_OF_WEEK
        header = ru.admin_out_of_week_header(author, incoming.text, kind.value)
        week_id = None
        letter = await letters.create(
            session,
            season_id=season.id,
            user_id=principal.user.id,
            source=letters.Source.OUT_OF_WEEK,
            text=incoming.text,
            report_id=result.report_id,
            now=now,
        )
        letter_id: int | None = letter.id
    else:
        week = await content.week_by_number(session, season.id, result.week_number)
        assert week is not None
        message = ru.report_reply(
            week, result.level, stamp_level=result.stamp_level, freeze_granted=result.freeze_granted
        )
        header = ru.admin_report_header(week.number, author, incoming.text, kind.value)
        week_id = week.id
        letter_id = None
    await _notify_admin(
        session,
        settings,
        principal,
        header,
        media_ids=result.media_ids,
        report_id=result.report_id,
        week_id=week_id,
        letter_id=letter_id,
        now=now,
    )
    await notify.enqueue_message(session, chat_id=principal.user.id, text=message, now=now)
    return schemas.ReportResult(
        report_id=result.report_id,
        week_number=result.week_number,
        out_of_week=result.out_of_week,
        level=result.level.value,
        stamp_level=result.stamp_level.value if result.stamp_level else None,
        freeze_granted=result.freeze_granted,
        message=message,
    )


def _incoming_file(upload: UploadFile) -> IncomingFile:
    return IncomingFile(
        kind=media_service.kind_for_mime(upload.content_type),
        file_id=None,
        mime=upload.content_type or "application/octet-stream",
        size=upload.size,
    )


async def _store_uploads(
    session: SessionDep,
    media_store: MediaStoreDep,
    media_ids: Sequence[uuid.UUID],
    uploads: Sequence[UploadFile],
    now: NowDep,
) -> None:
    """Stream every upload into place; if one fails, the ones already written are removed,
    because the transaction (and their rows) will be rolled back with the error."""
    stored: list[str] = []
    try:
        for media_id, upload in zip(media_ids, uploads, strict=True):
            saved = await media_store.receive_upload(
                session, media_id, _chunks(upload), now=now, max_bytes=MAX_UPLOAD_BYTES
            )
            stored.append(saved.path)
    except media_service.UploadTooLargeError as exc:
        _discard(media_store, stored)
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ"
        ) from exc
    except BaseException:
        _discard(media_store, stored)
        raise


def _discard(media_store: MediaStoreDep, paths: Sequence[str]) -> None:
    for relative in paths:
        media_store.full_path(relative).unlink(missing_ok=True)


async def _already_submitted(session: SessionDep, season: SeasonDep, row: models.Report) -> schemas.ReportResult:
    """The answer to a retried submission: what the first attempt produced, sent nowhere again."""
    week = await session.get(models.Week, row.week_id) if row.week_id is not None else None
    if week is None:
        return schemas.ReportResult(
            report_id=row.id,
            week_number=None,
            out_of_week=True,
            level=row.level,
            stamp_level=None,
            freeze_granted=False,
            message=ru.OUT_OF_WEEK,
        )
    stamp = await stamps.get_level(session, user_id=row.user_id, week_id=week.id)
    week_dto = await content.week_by_number(session, season.id, week.number)
    assert week_dto is not None
    return schemas.ReportResult(
        report_id=row.id,
        week_number=week.number,
        out_of_week=False,
        level=row.level,
        stamp_level=stamp.value if stamp else None,
        freeze_granted=False,
        message=ru.report_reply(week_dto, StampLevel(row.level), stamp_level=stamp, freeze_granted=False),
    )


@router.patch("/reports/{report_id}", response_model=schemas.ReportEditOut)
async def edit_report(
    report_id: int,
    principal: PrincipalDep,
    session: SessionDep,
    season: SeasonDep,
    settings: SettingsDep,
    media_store: MediaStoreDep,
    today: TodayDep,
    now: NowDep,
    request: Request,
) -> schemas.ReportEditOut:
    """Change the text and the files of one's own report while its week is open (DOMAIN §2).

    Multipart: `text`, any number of `remove` (media ids to hide), `files` to add and an
    `edit_key` the client makes up per attempt — a retry with the same key returns the report
    as it is instead of adding the files twice. Removed files are hidden, not deleted; the
    week's stamp is recomputed like after «это не отчёт». Mila gets a copy of the change
    (with the new files), the participant a receipt.
    """
    fields, lists, uploads = await _multipart(request)
    text = fields.get("text", "")
    if len(text.strip()) > MAX_TEXT_CHARS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"текст длиннее {MAX_TEXT_CHARS} знаков")
    edit_key = _attempt_key(fields, "edit_key")
    remove = [raw for raw in lists.get("remove", []) if raw]
    if len(remove) > MAX_UPLOAD_FILES * 2:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "слишком много файлов на удаление")
    try:
        remove_ids = [uuid.UUID(raw) for raw in remove]
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "неверный id файла") from exc
    row = await session.get(models.Report, report_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "такого отчёта нет")
    if row.user_id != principal.user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, ru.NOT_REPORT_FOREIGN)
    if edit_key:
        await _serialise(session, f"{principal.user.id}:edit:{report_id}:{edit_key}")
        await session.refresh(row)  # the winner of the race may have changed it while we waited
        if await reports.edit_applied(session, report_id=report_id, edit_key=edit_key):
            fresh = await views.report_out(session, report_id, today=today)
            assert fresh is not None
            level = await stamps.level_for_week(session, user_id=principal.user.id, week_id=row.week_id)
            week_dto = next((w for w in await content.weeks(session, season.id) if w.id == row.week_id), None)
            return schemas.ReportEditOut(
                report=fresh,
                stamp_level=level.value if level else None,
                freeze_granted=False,
                message=ru.edit_reply(week_dto, level, freeze_granted=False) if week_dto else "",
            )
    live = len([m for m in await _report_media(session, report_id) if m.hidden_at is None and m.id not in remove_ids])
    if live + len(uploads) > MAX_UPLOAD_FILES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"не больше {MAX_UPLOAD_FILES} файлов в отчёте")

    result = await reports.edit(
        session,
        user_id=principal.user.id,
        report_id=report_id,
        text=text,
        new_files=[_incoming_file(upload) for upload in uploads],
        remove_media_ids=remove_ids,
        now=now,
        edit_key=edit_key,
    )
    if not result.ok:
        if result.reason == reports.NOT_YOURS:
            raise HTTPException(status.HTTP_403_FORBIDDEN, ru.NOT_REPORT_FOREIGN)
        if result.reason == reports.CANCELLED:
            raise HTTPException(status.HTTP_409_CONFLICT, ru.NOT_REPORT_ALREADY)
        if result.reason == reports.EMPTY:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "пустой отчёт: нужен текст или файл")
        raise HTTPException(status.HTTP_409_CONFLICT, ru.EDIT_WEEK_OVER)
    await _store_uploads(session, media_store, result.media_ids, uploads, now)

    assert result.week_number is not None
    week = await content.week_by_number(session, season.id, result.week_number)
    assert week is not None
    message = ru.edit_reply(week, result.stamp_level, freeze_granted=result.freeze_granted)
    await _notify_admin(
        session,
        settings,
        principal,
        ru.admin_edit_header(
            week.number,
            principal.user.display_name_with_username,
            (text or "").strip() or None,
            added=len(result.media_ids),
            removed=result.removed,
        ),
        media_ids=result.media_ids,
        report_id=report_id,
        week_id=week.id,
        now=now,
    )
    await notify.enqueue_message(session, chat_id=principal.user.id, text=message, now=now)
    fresh = await views.report_out(session, report_id, today=today)
    assert fresh is not None
    return schemas.ReportEditOut(
        report=fresh,
        stamp_level=result.stamp_level.value if result.stamp_level else None,
        freeze_granted=result.freeze_granted,
        message=message,
    )


async def _report_media(session: SessionDep, report_id: int) -> list[models.Media]:
    query = select(models.Media).where(models.Media.report_id == report_id)
    return list((await session.execute(query)).scalars())


@router.post("/reports/{report_id}/cancel", response_model=schemas.CancelOut)
async def cancel_report(
    report_id: int,
    principal: PrincipalDep,
    session: SessionDep,
    season: SeasonDep,
    settings: SettingsDep,
    now: NowDep,
) -> schemas.CancelOut:
    """«Это не отчёт, а сообщение Миле»: the stamp is recomputed, the text goes to Mila."""
    if await session.get(models.Report, report_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "такого отчёта нет")
    cancelled = await reports.cancel(session, user_id=principal.user.id, report_id=report_id, now=now)
    if not cancelled.ok:
        if cancelled.reason == "already_cancelled":
            return schemas.CancelOut(ok=False, stamp_level=None, message=ru.NOT_REPORT_ALREADY)
        raise HTTPException(status.HTTP_403_FORBIDDEN, ru.NOT_REPORT_FOREIGN)
    row = await session.get(models.Report, report_id)
    # A message sent outside a week is a letter already; taking it back adds nothing new.
    letter = await letters.for_report(session, report_id) or await letters.create(
        session,
        season_id=season.id,
        user_id=principal.user.id,
        source=letters.Source.NOT_REPORT,
        text=row.text if row else None,
        report_id=report_id,
        now=now,
    )
    await _notify_admin(
        session,
        settings,
        principal,
        ru.admin_letter_header(principal.user.display_name_with_username, row.text if row else None, corrected=True),
        report_id=report_id,
        letter_id=letter.id,
        now=now,
    )
    return schemas.CancelOut(
        ok=True,
        stamp_level=cancelled.stamp_level.value if cancelled.stamp_level else None,
        message=ru.NOT_REPORT_DONE,
    )


@router.post("/weeks/{week_number}/level", response_model=schemas.LevelOut)
async def fix_level(
    week_number: int,
    body: schemas.LevelIn,
    principal: PrincipalDep,
    session: SessionDep,
    season: SeasonDep,
    now: NowDep,
) -> schemas.LevelOut:
    """«Это был максимум/минимум»: upgrade only, and only with a report (DOMAIN §2)."""
    if await content.week_by_number(session, season.id, week_number) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such week")
    level = StampLevel(body.level)
    result = await reports.fix_level(
        session, season_id=season.id, user_id=principal.user.id, week_number=week_number, level=level, now=now
    )
    if result.ok:
        message = f"Поправила — засчитано как <b>{ru.level_name(level)}</b>."
    elif result.reason == reports.NO_DOWNGRADE:
        message = "Максимум не понижаю — звёздочка остаётся ⭐"
    else:
        message = "За эту неделю отчёта нет — пришли текст или фото."
    return schemas.LevelOut(
        ok=result.ok, stamp_level=result.stamp_level.value if result.stamp_level else None, message=message
    )


@router.post("/letters", response_model=schemas.MessageOut)
async def send_letter(
    body: schemas.TextIn,
    principal: PrincipalDep,
    session: SessionDep,
    season: SeasonDep,
    settings: SettingsDep,
    now: NowDep,
) -> schemas.MessageOut:
    """«Написать Миле»: not a report, no stamp; stored in her inbox, answered from the chat or the app."""
    letter = await letters.create(
        session,
        season_id=season.id,
        user_id=principal.user.id,
        source=letters.Source.APP,
        text=body.text,
        now=now,
    )
    await _notify_admin(
        session,
        settings,
        principal,
        ru.admin_letter_header(principal.user.display_name_with_username, body.text.strip()),
        letter_id=letter.id,
        now=now,
    )
    return schemas.MessageOut(message=ru.LETTER_SENT)


# --- dictionary and facts ----------------------------------------------------------


@router.get("/dictionary", response_model=schemas.DictionaryOut)
async def dictionary(
    principal: PrincipalDep, session: SessionDep, season: SeasonDep, today: TodayDep
) -> schemas.DictionaryOut:
    view = await words.season_dictionary(session, season.id, today=today)
    names = await people.display_names(session, [item.user_id for item in view.user_words], short=True)
    return schemas.DictionaryOut(
        about=season.title,
        week_words=[
            schemas.WeekWordOut(week_number=w.number, title=w.title, word=w.word, word_ru=w.word_ru, meaning=w.meaning)
            for w in view.week_words
        ],
        user_words=[
            schemas.UserWordOut(
                id=w.id,
                word=w.word,
                meaning=w.meaning,
                author=names.get(w.user_id, str(w.user_id)),
                mine=w.user_id == principal.user.id,
            )
            for w in view.user_words
        ],
    )


@router.post("/words", response_model=schemas.WordAdded, status_code=status.HTTP_201_CREATED)
async def add_word(
    body: schemas.TextIn,
    principal: PrincipalDep,
    session: SessionDep,
    season: SeasonDep,
    settings: SettingsDep,
    today: TodayDep,
    now: NowDep,
) -> schemas.WordAdded:
    """«Добавить своё слово» — «слово — значение» in one line; the first one earns a freeze."""
    week = await content.current_week(session, season.id, today=today)
    result = await words.add(
        session,
        season_id=season.id,
        user_id=principal.user.id,
        week_id=week.id if week else None,
        raw=body.text.strip(),
        now=now,
    )
    await _notify_admin(
        session,
        settings,
        principal,
        ru.admin_word_added(
            principal.user.display_name_with_username, body.text.strip(), week.number if week else None
        ),
        now=now,
    )
    return schemas.WordAdded(
        word=result.word,
        meaning=result.meaning,
        freeze_granted=result.freeze_granted,
        message=ru.WORD_SAVED + (ru.WORD_FREEZE_BONUS if result.freeze_granted else ""),
    )


@router.get("/facts", response_model=schemas.FactsOut)
async def list_facts(principal: PrincipalDep, session: SessionDep, season: SeasonDep) -> schemas.FactsOut:
    listed = await facts.list_active(session, season.id)
    names = await people.display_names(session, [f.author_id for f in listed if f.author_id is not None], short=True)
    return schemas.FactsOut(
        about=season.title_accusative or season.title,
        facts=[
            schemas.FactItem(
                id=f.id,
                text=f.text,
                author=names.get(f.author_id) if f.author_id is not None else None,
                mine=f.author_id == principal.user.id,
                created_at=f.created_at,
            )
            for f in listed
        ],
    )


@router.post("/facts", response_model=schemas.MessageOut, status_code=status.HTTP_201_CREATED)
async def add_fact(
    body: schemas.TextIn,
    principal: PrincipalDep,
    session: SessionDep,
    season: SeasonDep,
    settings: SettingsDep,
    today: TodayDep,
    now: NowDep,
) -> schemas.MessageOut:
    """«Добавить свой факт»; Mila's own facts carry no author, like in the bot."""
    week = await content.current_week(session, season.id, today=today)
    await facts.add(
        session,
        season_id=season.id,
        week_id=week.id if week else None,
        text=body.text.strip(),
        author_id=None if principal.is_admin else principal.user.id,
        now=now,
    )
    if principal.is_admin:
        total = len(await facts.list_active(session, season.id))
        return schemas.MessageOut(message=f"Записала. Фактов за сезон: <b>{total}</b>")
    await _notify_admin(
        session,
        settings,
        principal,
        ru.admin_fact_added(
            principal.user.display_name_with_username, body.text.strip(), week.number if week else None
        ),
        now=now,
    )
    return schemas.MessageOut(message=ru.FACT_SAVED)


# --- journal and PDF ---------------------------------------------------------------


@router.get("/journal", response_model=schemas.JournalOut)
async def my_journal(
    principal: PrincipalDep, session: SessionDep, season: SeasonDep, today: TodayDep
) -> schemas.JournalOut:
    await people.ensure_member(session, season.id, principal.user.id, now=principal.user.joined_at)
    return await views.journal_out(
        session, season=season, user_id=principal.user.id, today=today, principal_admin=principal.is_admin
    )


@router.post("/journal/pdf", response_model=schemas.JobOut, status_code=status.HTTP_202_ACCEPTED)
async def request_pdf(principal: PrincipalDep, session: SessionDep, season: SeasonDep, now: NowDep) -> schemas.JobOut:
    job_id = await jobs.enqueue(
        session,
        "journal_pdf",
        {"user_id": principal.user.id, "season_id": season.id, "chat_id": principal.user.id, "requested_via": "web"},
        now=now,
    )
    return schemas.JobOut(job_id=job_id, status="queued")


async def _own_job(job_id: int, principal: PrincipalDep, session: SessionDep) -> jobs.JobDetail:
    job = await jobs.get(session, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
    if job.payload.get("user_id") != principal.user.id and not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your journal")
    return job


@router.get("/journal/pdf/{job_id}", response_model=schemas.JobOut)
async def pdf_status(job_id: int, principal: PrincipalDep, session: SessionDep) -> schemas.JobOut:
    job = await _own_job(job_id, principal, session)
    url = f"/api/journal/pdf/{job_id}/file" if job.status == "done" and job.payload.get("result_path") else None
    return schemas.JobOut(
        job_id=job.id, status=job.status, url=url, error=job.error if job.status == "failed" else None
    )


@router.get("/journal/pdf/{job_id}/file")
async def pdf_file(
    job_id: int, principal: PrincipalDep, session: SessionDep, media_store: MediaStoreDep
) -> FileResponse:
    job = await _own_job(job_id, principal, session)
    relative = job.payload.get("result_path")
    if job.status != "done" or not relative:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the PDF is not ready")
    path = media_store.full_path(str(relative))
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the PDF file is gone")
    return FileResponse(path, media_type="application/pdf", filename=path.name, headers={"Cache-Control": "private"})


# --- helpers -----------------------------------------------------------------------


async def _notify_admin(
    session: SessionDep,
    settings: SettingsDep,
    principal: Principal,
    text: str,
    *,
    media_ids: Sequence[uuid.UUID] = (),
    report_id: int | None = None,
    week_id: int | None = None,
    letter_id: int | None = None,
    now: NowDep,
) -> None:
    """Queue a copy for Mila the way the bot sends it; silent for Mila's own actions."""
    admin_chat = settings.admin_chat
    if admin_chat is None or principal.user.id == admin_chat:
        return
    await notify.enqueue_message(
        session,
        chat_id=admin_chat,
        text=text,
        media_ids=media_ids,
        link_user_id=principal.user.id,
        link_report_id=report_id,
        link_week_id=week_id,
        link_letter_id=letter_id,
        now=now,
    )
