"""Mila adds, moves and deletes weeks herself (DOMAIN §1, 14.09.2026).

The calendar rules: only into the future, no overlap, no deleting what participants touched.
"""

# ruff: noqa: F811 — the `season` and `app` fixtures are imported from sibling modules on purpose

from __future__ import annotations

import re
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.services import content, people
from tests.integration.bot_harness import ADMIN_ID, ALICE
from tests.integration.test_services_edges import season  # noqa: F401
from tests.integration.test_web_miniapp import App, app, moscow  # noqa: F401

TODAY = date(2026, 9, 2)  # week 1 (31.08–06.09) is running in the fixture season


async def _audit(session: AsyncSession, entity_id: int) -> list[models.AuditLog]:
    rows = await session.execute(
        select(models.AuditLog)
        .where(models.AuditLog.entity == "week", models.AuditLog.entity_id == str(entity_id))
        .order_by(models.AuditLog.id)
    )
    return list(rows.scalars())


# --- create ------------------------------------------------------------------------


async def test_create_week_after_the_season_and_it_is_listed(db_session: AsyncSession, season: int) -> None:
    week = await content.create_week(
        db_session,
        actor_id=ADMIN_ID,
        season_id=season,
        number=13,
        starts_on=date(2026, 11, 23),
        ends_on=date(2026, 11, 29),
        today=TODAY,
        texts={"title": "Эпилог"},
    )
    assert week.number == 13 and week.title == "Эпилог" and week.task_min == ""
    numbers = [w.number for w in await content.weeks(db_session, season)]
    assert numbers == list(range(1, 14))
    log = await _audit(db_session, week.id)
    assert [row.action for row in log] == ["create"]
    assert log[0].before is None and log[0].after is not None and log[0].after["title"] == "Эпилог"


async def test_create_week_refuses_today_or_the_past(db_session: AsyncSession, season: int) -> None:
    with pytest.raises(content.ContentError, match="not after today"):
        await content.create_week(
            db_session,
            actor_id=ADMIN_ID,
            season_id=season,
            number=13,
            starts_on=TODAY,
            ends_on=date(2026, 9, 8),
            today=TODAY,
        )


async def test_create_week_refuses_overlap_and_a_taken_number(db_session: AsyncSession, season: int) -> None:
    with pytest.raises(content.ContentError, match="overlaps"):
        await content.create_week(
            db_session,
            actor_id=ADMIN_ID,
            season_id=season,
            number=13,
            starts_on=date(2026, 9, 10),  # inside week 2 (07.09–13.09)
            ends_on=date(2026, 9, 12),
            today=TODAY,
        )
    with pytest.raises(content.ContentError, match="already taken"):
        await content.create_week(
            db_session,
            actor_id=ADMIN_ID,
            season_id=season,
            number=2,
            starts_on=date(2026, 11, 23),
            ends_on=date(2026, 11, 29),
            today=TODAY,
        )
    # The refused inserts must not poison the session: the calendar is still usable.
    assert len(await content.weeks(db_session, season)) == 12


async def test_create_week_refuses_bad_dates_and_unknown_texts(db_session: AsyncSession, season: int) -> None:
    with pytest.raises(ValueError, match="before it starts"):
        await content.create_week(
            db_session,
            actor_id=ADMIN_ID,
            season_id=season,
            number=13,
            starts_on=date(2026, 11, 29),
            ends_on=date(2026, 11, 23),
            today=TODAY,
        )
    with pytest.raises(ValueError, match="not editable"):
        await content.create_week(
            db_session,
            actor_id=ADMIN_ID,
            season_id=season,
            number=13,
            starts_on=date(2026, 11, 23),
            ends_on=date(2026, 11, 29),
            today=TODAY,
            texts={"starts_on": "2027-01-01"},
        )


# --- move --------------------------------------------------------------------------


async def test_move_a_future_week_and_log_it(db_session: AsyncSession, season: int) -> None:
    week12 = await content.week_by_number(db_session, season, 12)
    assert week12 is not None
    moved = await content.move_week(
        db_session,
        actor_id=ADMIN_ID,
        week_id=week12.id,
        today=TODAY,
        starts_on=date(2026, 11, 23),
        ends_on=date(2026, 11, 25),
    )
    assert (moved.starts_on, moved.ends_on, moved.number) == (date(2026, 11, 23), date(2026, 11, 25), 12)
    log = await _audit(db_session, week12.id)
    assert [row.action for row in log] == ["move"]
    assert log[0].before == {"starts_on": "2026-11-16", "ends_on": "2026-11-18"}, "only what changed is logged"
    assert log[0].after == {"starts_on": "2026-11-23", "ends_on": "2026-11-25"}


async def test_move_refuses_a_started_week_and_a_move_into_the_past(db_session: AsyncSession, season: int) -> None:
    week1 = await content.week_by_number(db_session, season, 1)
    week12 = await content.week_by_number(db_session, season, 12)
    assert week1 is not None and week12 is not None
    with pytest.raises(content.ContentError, match="calendar is frozen"):
        await content.move_week(db_session, actor_id=ADMIN_ID, week_id=week1.id, today=TODAY, ends_on=date(2026, 9, 7))
    with pytest.raises(content.ContentError, match="not after today"):
        await content.move_week(
            db_session,
            actor_id=ADMIN_ID,
            week_id=week12.id,
            today=TODAY,
            starts_on=date(2026, 8, 24),
            ends_on=date(2026, 8, 30),
        )


async def test_move_refuses_overlap_and_a_noop_writes_nothing(db_session: AsyncSession, season: int) -> None:
    week12 = await content.week_by_number(db_session, season, 12)
    assert week12 is not None
    with pytest.raises(content.ContentError, match="overlaps"):
        await content.move_week(
            db_session,
            actor_id=ADMIN_ID,
            week_id=week12.id,
            today=TODAY,
            starts_on=date(2026, 11, 10),  # inside week 11
            ends_on=date(2026, 11, 12),
        )
    same = await content.move_week(db_session, actor_id=ADMIN_ID, week_id=week12.id, today=TODAY, number=12)
    assert same.starts_on == date(2026, 11, 16)
    assert await _audit(db_session, week12.id) == []


# --- delete ------------------------------------------------------------------------


async def test_delete_an_untouched_future_week(db_session: AsyncSession, season: int) -> None:
    week12 = await content.week_by_number(db_session, season, 12)
    assert week12 is not None
    await content.delete_week(db_session, actor_id=ADMIN_ID, week_id=week12.id, today=TODAY)
    assert await content.week_by_number(db_session, season, 12) is None
    assert len(await content.weeks(db_session, season)) == 11
    log = await _audit(db_session, week12.id)
    assert [row.action for row in log] == ["delete"]
    assert log[0].after is None and log[0].before is not None and log[0].before["title"] == week12.title


async def test_delete_refuses_a_started_week(db_session: AsyncSession, season: int) -> None:
    week1 = await content.week_by_number(db_session, season, 1)
    assert week1 is not None
    with pytest.raises(content.ContentError, match="is not deleted"):
        await content.delete_week(db_session, actor_id=ADMIN_ID, week_id=week1.id, today=TODAY)
    assert await content.week_by_number(db_session, season, 1) is not None


async def test_delete_refuses_a_week_with_participant_data(db_session: AsyncSession, season: int) -> None:
    """CLAUDE.md rule 1: a week someone touched is never deleted, however far in the future."""
    week2 = await content.week_by_number(db_session, season, 2)
    assert week2 is not None
    # An intent is the lightest trace a participant can leave; the rule catches it too.
    await people.set_intent(
        db_session,
        season_id=season,
        user_id=ALICE,
        week_id=week2.id,
        choice=models.IntentChoice.TAKE,
        now=moscow(2026, 9, 1),
    )
    with pytest.raises(content.ContentError, match="participant data"):
        await content.delete_week(db_session, actor_id=ADMIN_ID, week_id=week2.id, today=date(2026, 9, 1))
    assert await content.week_by_number(db_session, season, 2) is not None


async def test_delete_unknown_week(db_session: AsyncSession, season: int) -> None:
    with pytest.raises(content.ContentError, match="does not exist"):
        await content.delete_week(db_session, actor_id=ADMIN_ID, week_id=999_999, today=TODAY)


# --- through the admin API ---------------------------------------------------------


async def test_admin_api_create_move_delete_round_trip(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    r = await app.client.post(
        "/api/admin/weeks",
        json={"number": 13, "starts_on": "2026-11-23", "ends_on": "2026-11-29", "title": "Эпилог"},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["number"] == 13 and created["title"] == "Эпилог" and created["state"] == "locked"

    r = await app.client.patch(
        f"/api/admin/weeks/{created['id']}/calendar",
        json={"starts_on": "2026-11-30", "ends_on": "2026-12-06"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["starts_on"] == "2026-11-30" and r.json()["number"] == 13

    r = await app.client.delete(f"/api/admin/weeks/{created['id']}", headers=admin)
    assert r.status_code == 204
    numbers = [w["number"] for w in (await app.client.get("/api/admin/weeks", headers=admin)).json()]
    assert numbers == list(range(1, 13))


async def test_admin_api_calendar_refusals_have_the_right_codes(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    weeks = (await app.client.get("/api/admin/weeks", headers=admin)).json()
    current, future = weeks[0], weeks[-1]

    r = await app.client.post(
        "/api/admin/weeks", json={"number": 2, "starts_on": "2026-11-23", "ends_on": "2026-11-29"}, headers=admin
    )
    assert r.status_code == 409 and "already taken" in r.json()["detail"]

    r = await app.client.post(
        "/api/admin/weeks", json={"number": 13, "starts_on": "2026-11-29", "ends_on": "2026-11-23"}, headers=admin
    )
    assert r.status_code == 422

    r = await app.client.patch(
        f"/api/admin/weeks/{current['id']}/calendar", json={"ends_on": "2026-09-07"}, headers=admin
    )
    assert r.status_code == 409 and "frozen" in r.json()["detail"]

    r = await app.client.patch(f"/api/admin/weeks/{future['id']}/calendar", json={}, headers=admin)
    assert r.status_code == 422

    assert (await app.client.delete(f"/api/admin/weeks/{current['id']}", headers=admin)).status_code == 409
    assert (await app.client.delete("/api/admin/weeks/999999", headers=admin)).status_code == 404
    r = await app.client.patch("/api/admin/weeks/999999/calendar", json={"number": 5}, headers=admin)
    assert r.status_code == 404


async def test_admin_api_calendar_is_admin_only(app: App) -> None:
    body = {"number": 13, "starts_on": "2026-11-23", "ends_on": "2026-11-29"}
    assert (await app.client.post("/api/admin/weeks", json=body)).status_code == 401
    assert (await app.client.post("/api/admin/weeks", json=body, headers=app.headers(ALICE))).status_code == 403
    assert (await app.client.delete("/api/admin/weeks/1", headers=app.headers(ALICE))).status_code == 403


# --- a gap in the numbering must not shift what participants see --------------------


async def test_pdf_grid_and_season_page_follow_real_week_numbers(app: App) -> None:
    """Deleting week 7 must not draw a phantom cell 7 and lose cell 12 (code review, 14.09)."""
    from romantika.pdf.journal import render_journal_html
    from romantika.services import journal

    admin = app.headers(ADMIN_ID, "Мила")
    weeks = {w["number"]: w for w in (await app.client.get("/api/admin/weeks", headers=admin)).json()}
    assert (await app.client.delete(f"/api/admin/weeks/{weeks[7]['id']}", headers=admin)).status_code == 204

    view = await journal.build(app.session, season_id=app.season_id, user_id=ALICE, today=app.now.date())
    assert view.weeks_total == 11 and view.week_numbers == [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12]
    html = render_journal_html(view)
    cells = re.findall(r'<span class="num">(\d+)</span>', html)
    assert cells == ["1", "2", "3", "4", "5", "6", "8", "9", "10", "11", "12"], "no phantom 7, no lost 12"

    page = (await app.client.get("/")).text
    assert page.count('class="seg') == 11, "one segment per real week, not per index"
    assert "seg now" in page, "the running week keeps its «now» segment"


async def test_delete_refuses_a_week_a_reply_link_points_at(db_session: AsyncSession, season: int) -> None:
    """The sixth FK to weeks: Mila's reply routing. Refused with a reason, not a 500."""
    from romantika.services import links

    week12 = await content.week_by_number(db_session, season, 12)
    assert week12 is not None
    await links.remember(
        db_session,
        admin_chat_id=ADMIN_ID,
        admin_message_id=777,
        user_id=ALICE,
        report_id=None,
        week_id=week12.id,
        now=moscow(2026, 9, 1),
    )
    with pytest.raises(content.ContentError, match="participant data"):
        await content.delete_week(db_session, actor_id=ADMIN_ID, week_id=week12.id, today=TODAY)
    assert await content.week_by_number(db_session, season, 12) is not None
