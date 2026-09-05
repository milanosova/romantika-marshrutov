"""The JSON formatter must never drop a record because of an exotic extra."""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid

from romantika.logging import JsonFormatter


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("romantika.test", logging.INFO, __file__, 1, "готово", None, None)
    record.__dict__.update(extra)
    return record


def test_plain_extras_are_top_level_fields() -> None:
    payload = json.loads(JsonFormatter().format(make_record(report_id=7, season="mexico-2026")))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "romantika.test"
    assert payload["message"] == "готово"
    assert payload["report_id"] == 7
    assert payload["season"] == "mexico-2026"


def test_uuid_date_and_datetime_extras_are_stringified() -> None:
    media_id = uuid.uuid4()
    day = dt.date(2026, 1, 15)
    moment = dt.datetime(2026, 1, 15, 12, 30, tzinfo=dt.UTC)

    payload = json.loads(JsonFormatter().format(make_record(media_id=media_id, day=day, at=moment)))

    assert payload["media_id"] == str(media_id)
    assert payload["day"] == "2026-01-15"
    assert payload["at"] == str(moment)


def test_unserialisable_extra_does_not_lose_the_record() -> None:
    class Opaque:
        def __str__(self) -> str:
            return "opaque"

    payload = json.loads(JsonFormatter().format(make_record(thing=Opaque())))

    assert payload["message"] == "готово"
    assert payload["thing"] == "opaque"


def test_non_ascii_is_not_escaped() -> None:
    assert "готово" in JsonFormatter().format(make_record())
