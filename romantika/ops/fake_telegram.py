"""A stand-in for the Bot API for the local stand: `python -m romantika.ops.fake_telegram`.

The bot polls it, the worker delivers through it, nobody reaches Telegram. Test "participants"
(people or agents) talk to it through `/_control/*`: push a text, a photo or a button press as
a user, read what the bot sent to a chat. Everything lives in memory; restart = clean slate.

Bot API surface: what aiogram calls in this project (see `bot/`, `worker/`, `ops/telegram_setup`).
Unknown methods answer `{"ok": true, "result": true}` so a new call never blocks the stand;
they are logged so the omission is visible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("fake_telegram")

BOT_ID = 1000
BOT_USERNAME = "romantika_dev_bot"


@dataclass
class Store:
    updates: list[dict[str, Any]] = field(default_factory=list)
    next_update_id: int = 1
    next_message_id: int = 1
    sent: list[dict[str, Any]] = field(default_factory=list)
    files: dict[str, tuple[bytes, str]] = field(default_factory=dict)  # file_id → (bytes, file_path)
    users: dict[int, dict[str, Any]] = field(default_factory=dict)
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def message_id(self) -> int:
        self.next_message_id += 1
        return self.next_message_id

    def push(self, update: dict[str, Any]) -> int:
        update_id = self.next_update_id
        self.next_update_id += 1
        self.updates.append({"update_id": update_id, **update})
        self.wake.set()
        return update_id

    def user(self, user_id: int, name: str | None = None, username: str | None = None) -> dict[str, Any]:
        if user_id not in self.users or name:
            self.users[user_id] = {
                "id": user_id,
                "is_bot": False,
                "first_name": name or self.users.get(user_id, {}).get("first_name") or f"User{user_id}",
                **({"username": username} if username else {}),
                "language_code": "ru",
            }
        return self.users[user_id]

    def chat(self, user_id: int) -> dict[str, Any]:
        user = self.user(user_id)
        return {
            "id": user_id,
            "type": "private",
            "first_name": user["first_name"],
            **({"username": user["username"]} if "username" in user else {}),
        }


store = Store()
app = FastAPI(title="fake Bot API")


async def _params(request: Request) -> dict[str, Any]:
    """aiogram sends multipart form data; other clients may send JSON. Nested values are JSON."""
    content_type = request.headers.get("content-type", "")
    params: dict[str, Any] = {}
    files: dict[str, tuple[bytes, str]] = {}
    if content_type.startswith("application/json"):
        params = await request.json()
    else:
        form = await request.form()
        for key, value in form.multi_items():
            if hasattr(value, "read"):
                files[key] = (await value.read(), getattr(value, "filename", None) or key)
            else:
                text = str(value)
                if text[:1] in "{[":
                    try:
                        params[key] = json.loads(text)
                        continue
                    except ValueError:
                        pass
                params[key] = text
    params["__files__"] = files
    return params


def _ok(result: Any) -> JSONResponse:
    return JSONResponse({"ok": True, "result": result})


def _error(code: int, description: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error_code": code, "description": description}, status_code=code)


def _message(chat_id: int, **fields: Any) -> dict[str, Any]:
    return {
        "message_id": store.message_id(),
        "date": int(time.time()),
        "chat": store.chat(chat_id),
        "from": {"id": BOT_ID, "is_bot": True, "first_name": "Романтика маршрутов", "username": BOT_USERNAME},
        **fields,
    }


def _remember_file(data: bytes, filename: str, kind: str) -> dict[str, Any]:
    file_id = f"{kind}-{len(store.files) + 1}"
    suffix = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
    path = f"{kind}s/{file_id}{suffix}"
    store.files[file_id] = (data, path)
    return {"file_id": file_id, "file_unique_id": file_id, "file_size": len(data)}


def _attach(fields: dict[str, Any], key: str, kind: str, files: dict[str, tuple[bytes, str]]) -> None:
    """Fill the media field of an outgoing message from the uploaded file (or an existing id)."""
    raw = fields.pop(key, None)
    if key in files:
        data, name = files[key]
    elif isinstance(raw, str) and raw.startswith("attach://") and raw[9:] in files:
        data, name = files[raw[9:]]
    elif isinstance(raw, str) and raw in store.files:
        fields[kind] = (
            [{**_meta(raw), "width": 800, "height": 600}] if kind == "photo" else {**_meta(raw), **_extra(kind)}
        )
        return
    else:
        data, name = b"", "file.bin"
    meta = _remember_file(data, name, kind)
    fields[kind] = (
        [{**meta, "width": 800, "height": 600}] if kind == "photo" else {**meta, **_extra(kind), "file_name": name}
    )


def _extra(kind: str) -> dict[str, Any]:
    """The fields Telegram always sends for a media kind (aiogram refuses the answer without them)."""
    if kind in ("video", "animation", "video_note"):
        return {"width": 640, "height": 480, "duration": 3, "length": 240}
    if kind in ("audio", "voice"):
        return {"duration": 3}
    return {}


def _meta(file_id: str) -> dict[str, Any]:
    data, _ = store.files[file_id]
    return {"file_id": file_id, "file_unique_id": file_id, "file_size": len(data)}


@app.post("/bot{token}/{method}")
async def bot_api(token: str, method: str, request: Request) -> Response:
    params = await _params(request)
    files = params.pop("__files__")
    if method == "getMe":
        return _ok(
            {
                "id": BOT_ID,
                "is_bot": True,
                "first_name": "Романтика маршрутов",
                "username": BOT_USERNAME,
                "can_join_groups": False,
                "can_read_all_group_messages": False,
                "supports_inline_queries": False,
            }
        )
    if method == "getUpdates":
        offset = int(params.get("offset") or 0)
        timeout = float(params.get("timeout") or 0)
        deadline = time.monotonic() + timeout
        while True:
            pending = [u for u in store.updates if u["update_id"] >= offset]
            if pending or time.monotonic() >= deadline:
                store.updates = pending
                store.wake.clear()
                return _ok(pending[:100])
            store.wake.clear()
            try:
                await asyncio.wait_for(store.wake.wait(), timeout=max(0.05, deadline - time.monotonic()))
            except TimeoutError:
                pass
    if method == "getFile":
        file_id = str(params.get("file_id"))
        if file_id not in store.files:
            return _error(400, "Bad Request: file not found")
        data, path = store.files[file_id]
        return _ok({"file_id": file_id, "file_unique_id": file_id, "file_size": len(data), "file_path": path})
    if method in {
        "sendMessage",
        "sendPhoto",
        "sendVideo",
        "sendDocument",
        "sendVoice",
        "sendAudio",
        "sendVideoNote",
        "sendAnimation",
    }:
        chat_id = int(params["chat_id"])
        fields: dict[str, Any] = {}
        if method == "sendMessage":
            fields["text"] = params.get("text", "")
        else:
            key = {
                "sendPhoto": "photo",
                "sendVideo": "video",
                "sendDocument": "document",
                "sendVoice": "voice",
                "sendAudio": "audio",
                "sendVideoNote": "video_note",
                "sendAnimation": "animation",
            }[method]
            fields[key] = params.get(key)
            _attach(fields, key, key, files)
            if params.get("caption"):
                fields["caption"] = params["caption"]
        markup = params.get("reply_markup")
        if isinstance(markup, dict) and "inline_keyboard" in markup:
            fields["reply_markup"] = markup  # Telegram echoes inline keyboards only, never the reply one
        if params.get("reply_to_message_id") or params.get("reply_parameters"):
            fields["reply_to_message"] = {
                "message_id": int(params.get("reply_to_message_id") or params["reply_parameters"]["message_id"]),
                "date": 0,
                "chat": store.chat(chat_id),
            }
        message = _message(chat_id, **fields)
        store.sent.append({"method": method, "chat_id": chat_id, "message": message, "at": time.time()})
        return _ok(message)
    if method == "copyMessage":
        chat_id = int(params["chat_id"])
        message = _message(chat_id, text=f"[copy of {params.get('from_chat_id')}/{params.get('message_id')}]")
        store.sent.append({"method": method, "chat_id": chat_id, "message": message, "at": time.time()})
        return _ok({"message_id": message["message_id"]})
    if method in {"editMessageText", "editMessageReplyMarkup", "editMessageCaption"}:
        chat_id = int(params.get("chat_id") or 0)
        message = {
            "message_id": int(params.get("message_id") or 0),
            "date": int(time.time()),
            "chat": store.chat(chat_id),
            "text": params.get("text", ""),
            "reply_markup": params.get("reply_markup"),
        }
        store.sent.append({"method": method, "chat_id": chat_id, "message": message, "at": time.time()})
        return _ok(message)
    if method not in {
        "answerCallbackQuery",
        "deleteWebhook",
        "setMyCommands",
        "setMyName",
        "setMyDescription",
        "setMyShortDescription",
        "setChatMenuButton",
        "sendChatAction",
        "deleteMessage",
        "getWebhookInfo",
    }:
        logger.warning("unhandled method %s", method)
    if method == "answerCallbackQuery":
        store.sent.append(
            {
                "method": method,
                "chat_id": None,
                "message": {"text": params.get("text", ""), "show_alert": bool(params.get("show_alert"))},
                "at": time.time(),
            }
        )
    if method == "getWebhookInfo":
        return _ok({"url": "", "has_custom_certificate": False, "pending_update_count": 0})
    return _ok(True)


@app.get("/file/bot{token}/{path:path}")
async def file_download(token: str, path: str) -> Response:
    for data, file_path in store.files.values():
        if file_path == path:
            return Response(content=data, media_type="application/octet-stream")
    return Response(status_code=404)


# --- control: talk to the bot as a user ----------------------------------------------------


def _incoming(user_id: int, name: str | None, username: str | None, **fields: Any) -> dict[str, Any]:
    user = store.user(user_id, name, username)
    return {
        "message": {
            "message_id": store.message_id(),
            "date": int(time.time()),
            "chat": store.chat(user_id),
            "from": user,
            **fields,
        }
    }


@app.post("/_control/text")
async def control_text(request: Request) -> Response:
    """{"user_id", "text", "name"?, "username"?, "reply_to"?}: a text message from a user; `/start` too."""
    body = await request.json()
    fields: dict[str, Any] = {"text": body["text"]}
    if body["text"].startswith("/"):
        fields["entities"] = [{"type": "bot_command", "offset": 0, "length": len(body["text"].split()[0])}]
    if body.get("reply_to"):
        fields["reply_to_message"] = {
            "message_id": int(body["reply_to"]),
            "date": 0,
            "chat": store.chat(int(body["user_id"])),
            "text": "",
        }
    return _ok(
        {"update_id": store.push(_incoming(int(body["user_id"]), body.get("name"), body.get("username"), **fields))}
    )


@app.post("/_control/media")
async def control_media(request: Request) -> Response:
    """multipart: user_id, kind (photo|video|document|voice|video_note), file, caption?, name?."""
    form = await request.form()
    upload = form["file"]
    data = await upload.read()  # type: ignore[union-attr]
    kind = str(form.get("kind") or "photo")
    meta = _remember_file(data, getattr(upload, "filename", None) or "file.jpg", kind)
    fields: dict[str, Any] = {}
    if kind == "photo":
        fields["photo"] = [{**meta, "width": 320, "height": 240}, {**meta, "width": 1280, "height": 960}]
    elif kind == "document":
        fields["document"] = {
            **meta,
            "file_name": getattr(upload, "filename", None) or "file.bin",
            "mime_type": str(form.get("mime") or "application/octet-stream"),
        }
    elif kind == "video":
        fields["video"] = {**meta, "width": 1280, "height": 720, "duration": 5, "mime_type": "video/mp4"}
    elif kind == "voice":
        fields["voice"] = {**meta, "duration": 3, "mime_type": "audio/ogg"}
    elif kind == "video_note":
        fields["video_note"] = {**meta, "length": 240, "duration": 3}
    if form.get("caption"):
        fields["caption"] = str(form["caption"])
    user_id = int(str(form["user_id"]))
    return _ok({"update_id": store.push(_incoming(user_id, str(form.get("name") or "") or None, None, **fields))})


@app.post("/_control/callback")
async def control_callback(request: Request) -> Response:
    """{"user_id", "data", "message_id"?}: a press on an inline button the bot sent."""
    body = await request.json()
    user_id = int(body["user_id"])
    message_id = int(body.get("message_id") or 0)
    message = next(
        (
            s["message"]
            for s in reversed(store.sent)
            if s["chat_id"] == user_id and s["message"].get("message_id") == message_id
        ),
        None,
    ) or {
        "message_id": message_id or store.message_id(),
        "date": int(time.time()),
        "chat": store.chat(user_id),
        "text": "",
    }
    update = {
        "callback_query": {
            "id": str(store.message_id()),
            "from": store.user(user_id, body.get("name")),
            "message": message,
            "chat_instance": "1",
            "data": body["data"],
        }
    }
    return _ok({"update_id": store.push(update)})


@app.get("/_control/sent")
async def control_sent(chat_id: int | None = None, since: float = 0, limit: int = 50) -> Response:
    """What the bot sent: newest last. Filter by chat and by `at` (unix time)."""
    rows = [s for s in store.sent if (chat_id is None or s["chat_id"] == chat_id) and s["at"] > since]
    return _ok(rows[-limit:])


@app.post("/_control/reset")
async def control_reset() -> Response:
    store.sent.clear()
    store.updates.clear()
    return _ok(True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake Bot API for the local stand")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
