"""Pure helpers of the services layer: dictionary parsing, media paths, job backoff."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from romantika.domain.types import ReportKind
from romantika.services import jobs, media, words


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sobremesa — время за столом", ("sobremesa", "время за столом")),
        ("sobremesa - время за столом", ("sobremesa", "время за столом")),
        ("sobremesa – время за столом", ("sobremesa", "время за столом")),
        ("antojo: внезапное желание", ("antojo", "внезапное желание")),
        ("antojo : внезапное желание", ("antojo", "внезапное желание")),
        ("  fiesta  ", ("fiesta", "")),
        ("mano-a-mano — один на один", ("mano-a-mano", "один на один")),
        ("cielo — небо — и небеса", ("cielo", "небо — и небеса")),
        ("", ("", "")),
    ],
)
def test_word_parsing(raw: str, expected: tuple[str, str]) -> None:
    assert words.parse(raw) == expected


def test_word_parsing_takes_the_first_separator() -> None:
    """A colon inside the meaning does not steal the split from an earlier dash."""
    assert words.parse("alebrije — зверь из сна: голова ящерицы") == ("alebrije", "зверь из сна: голова ящерицы")


def test_media_suffix_falls_back_to_the_kind() -> None:
    assert media.suffix_for(kind=ReportKind.PHOTO, mime="image/jpeg") == ".jpg"
    assert media.suffix_for(kind=ReportKind.PHOTO, mime=None) == ".jpg"
    assert media.suffix_for(kind=ReportKind.VOICE, mime=None) == ".ogg"
    assert media.suffix_for(kind=ReportKind.DOCUMENT, mime=None) == ".bin"
    assert media.suffix_for(kind=ReportKind.DOCUMENT, mime="application/pdf") == ".pdf"


def test_media_path_is_unique_and_readable() -> None:
    first = media.new_relative_path(season_slug="mexico-2026", user_id=1001, suffix=".jpg")
    second = media.new_relative_path(season_slug="mexico-2026", user_id=1001, suffix=".jpg")
    assert first != second
    assert re.fullmatch(r"mexico-2026/1001/[0-9a-f-]{36}\.jpg", first)


def test_job_backoff_grows_exponentially() -> None:
    assert jobs.backoff_for(1) == timedelta(minutes=1)
    assert jobs.backoff_for(2) == timedelta(minutes=2)
    assert jobs.backoff_for(4) == timedelta(minutes=8)
    assert jobs.backoff_for(0) == timedelta(minutes=1), "never negative, never zero"
