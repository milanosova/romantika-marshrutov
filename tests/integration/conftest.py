"""Fixtures shared by the bot integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.bot_harness import Harness, build_harness


@pytest.fixture
async def harness(db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    """A dispatcher with the Mexico season active, week 1 running, Telegram recorded."""
    return await build_harness(db_session, tmp_path, monkeypatch)
