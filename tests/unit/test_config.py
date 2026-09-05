"""`MEDIA_DIR` is required: a default would point inside site-packages in a real install."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from romantika.config import DATA_DIR, Settings


def test_media_dir_is_taken_from_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))

    assert Settings(_env_file=None).media_dir == tmp_path / "media"


def test_missing_media_dir_fails_with_a_named_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEDIA_DIR", raising=False)

    with pytest.raises(ValidationError) as failure:
        Settings(_env_file=None)

    assert "media_dir" in str(failure.value)


def test_empty_media_dir_fails_with_an_explicit_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDIA_DIR", "  ")

    with pytest.raises(ValidationError) as failure:
        Settings(_env_file=None)

    assert "MEDIA_DIR must be set" in str(failure.value)


def test_packaged_data_dir_holds_the_season_content() -> None:
    assert (DATA_DIR / "tzolkin.json").is_file()
    assert (DATA_DIR / "seasons" / "mexico-2026.json").is_file()
