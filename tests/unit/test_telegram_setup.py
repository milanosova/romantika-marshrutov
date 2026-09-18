"""The bot applies its command list and menu button itself at every start (DOMAIN §7)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram.types import MenuButtonCommands, MenuButtonWebApp

from romantika.config import Settings
from romantika.ops.telegram_setup import apply_menu


def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base_url: str) -> Settings:
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("PUBLIC_BASE_URL", base_url)
    return Settings(_env_file=None)


async def test_two_commands_and_the_door_as_the_menu_button_over_https(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bot = AsyncMock()
    target = await apply_menu(bot, _settings(monkeypatch, tmp_path, "https://romantika.example.test/"))

    commands = bot.set_my_commands.call_args.args[0]
    assert [c.command for c in commands] == ["start", "help"]
    button = bot.set_chat_menu_button.call_args.kwargs["menu_button"]
    assert isinstance(button, MenuButtonWebApp)
    assert button.text == "🎒 Открыть клуб" and button.web_app.url == "https://romantika.example.test/app"
    assert target == "https://romantika.example.test/app"


async def test_menu_button_falls_back_to_commands_without_https(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Telegram refuses a web_app menu button over http: the stand must not fail at start."""
    bot = AsyncMock()
    await apply_menu(bot, _settings(monkeypatch, tmp_path, "http://127.0.0.1:8010"))

    assert isinstance(bot.set_chat_menu_button.call_args.kwargs["menu_button"], MenuButtonCommands)
