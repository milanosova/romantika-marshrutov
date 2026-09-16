"""The invented participants of the local stand come out of the same services as real ones."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.config import DATA_DIR
from romantika.db import models
from romantika.ops import demo_data
from romantika.services import content, seed
from romantika.services.media import MediaStore

SEASON_JSON = DATA_DIR / "seasons" / "mexico-2026.json"
TODAY = date(2026, 9, 16)  # week 3 of the Mexico season is running


async def _count(session: AsyncSession, model: type[models.Base]) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def test_populate_builds_a_believable_season(db_session: AsyncSession, tmp_path: Path) -> None:
    result = await seed.import_season(db_session, SEASON_JSON)
    await content.activate_season(db_session, result.season_id, actor_id=None)
    store = MediaStore(tmp_path / "media")

    counts = await demo_data.populate(db_session, store, today=TODAY, participants=10)

    assert counts["participants"] == 10
    assert counts["reports"] >= 8 and counts["photos"] >= 3
    assert counts["letters"] >= 1 and counts["facts"] == 3
    assert await demo_data._demo_users_present(db_session, 10) == 10
    admin = await db_session.get(models.User, demo_data.ADMIN_ID)
    assert admin is not None and admin.is_admin

    stamps = await _count(db_session, models.Stamp)
    assert stamps > 0
    media_rows = (await db_session.execute(select(models.Media))).scalars().all()
    assert media_rows and all(row.sha256 and row.downloaded_at for row in media_rows)
    assert all(store.full_path(row.path).exists() for row in media_rows)
    # The «frozen» persona has an extra freeze granted by the admin; the stars have an auto one.
    reasons = {row.reason for row in (await db_session.execute(select(models.Freeze))).scalars().all()}
    assert {"manual", "max"} <= reasons
    # Nobody reported in the future: every report sits before today.
    latest = (await db_session.execute(select(func.max(models.Report.created_at)))).scalar_one()
    assert latest.date() <= TODAY


async def test_populate_never_touches_real_looking_ids(db_session: AsyncSession, tmp_path: Path) -> None:
    result = await seed.import_season(db_session, SEASON_JSON)
    await content.activate_season(db_session, result.season_id, actor_id=None)
    await demo_data.populate(db_session, MediaStore(tmp_path / "media"), today=TODAY, participants=5)
    ids = set((await db_session.execute(select(models.User.id))).scalars().all())
    assert ids <= set(range(demo_data.FIRST_DEMO_ID, demo_data.FIRST_DEMO_ID + 5)) | {demo_data.ADMIN_ID}
