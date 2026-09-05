"""Telegram Mini App authentication (ARCHITECTURE §8.1).

Two ways in: the raw `initData` string in the `X-Telegram-Init-Data` header (validated with
the HMAC scheme from Telegram's docs), or the signed session cookie the page sets after its
first validated call — browsers cannot attach custom headers to `<img>` requests, so media
is fetched with the cookie.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode

INIT_DATA_HEADER = "X-Telegram-Init-Data"
SESSION_COOKIE = "rm_session"
INIT_DATA_MAX_AGE = timedelta(hours=24)
SESSION_MAX_AGE = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class InitDataUser:
    id: int
    first_name: str | None
    last_name: str | None
    username: str | None
    auth_date: datetime


def _secret(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def _check_string(fields: dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))


def build_init_data(bot_token: str, user: dict[str, object], *, auth_date: int, query_id: str = "AAEAAA") -> str:
    """Produce an `initData` string the way Telegram would — for tests and the dev bypass."""
    fields = {
        "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
        "auth_date": str(auth_date),
        "query_id": query_id,
    }
    digest = hmac.new(_secret(bot_token), _check_string(fields).encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def validate_init_data(init_data: str, bot_token: str, *, now: datetime) -> InitDataUser | None:
    """None when the signature is wrong, the data is stale, or the payload is malformed."""
    if not init_data or not bot_token:
        return None
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received = fields.pop("hash", None)
    if not received:
        return None
    expected = hmac.new(_secret(bot_token), _check_string(fields).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        auth_date = datetime.fromtimestamp(int(fields["auth_date"]), tz=UTC)
        user = json.loads(fields["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if now - auth_date > INIT_DATA_MAX_AGE or auth_date - now > timedelta(minutes=5):
        return None
    return InitDataUser(
        id=user_id,
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        username=user.get("username"),
        auth_date=auth_date,
    )


def make_session_token(bot_token: str, user_id: int, *, now: datetime) -> str:
    """`<user_id>.<expires>.<signature>` — the value of the session cookie."""
    expires = int((now + SESSION_MAX_AGE).timestamp())
    payload = f"{user_id}.{expires}"
    signature = hmac.new(_secret(bot_token), f"session:{payload}".encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def read_session_token(token: str | None, bot_token: str, *, now: datetime) -> int | None:
    if not token or not bot_token:
        return None
    try:
        user_id, expires, signature = token.split(".")
        payload = f"{user_id}.{expires}"
    except ValueError:
        return None
    expected = hmac.new(_secret(bot_token), f"session:{payload}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    if int(expires) < int(now.timestamp()):
        return None
    return int(user_id)
