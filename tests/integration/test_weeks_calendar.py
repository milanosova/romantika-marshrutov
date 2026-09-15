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
from romantika.domain.types import ReportKind, StampLevel
from romantika.services import content, people
from tests.integration.bot_harness import ADMIN_ID, ALICE
from tests.integration.test_services_edges import season  # noqa: F401
from tests.integration.test_web_miniapp import App, app, moscow  # noqa: F401

TODAY = date(2026, 9, 2)  # week 1 (31.08–06.09) is running in the fixture season
SLOT = (date(2026, 11, 16), date(2026, 11, 18))  # week 12's dates; the season ends 18.11


async def _free_last_slot(session: AsyncSession, season_id: int) -> int:
    """Delete untouched week 12 so its dates are free inside the season; returns its old id."""
    week12 = await content.week_by_number(session, season_id, 12)
    assert week12 is not None
    await content.delete_week(session, actor_id=ADMIN_ID, week_id=week12.id, today=TODAY)
    return week12.id


async def _audit(session: AsyncSession, entity_id: int) -> list[models.AuditLog]:
    rows = await session.execute(
        select(models.AuditLog)
        .where(models.AuditLog.entity == "week", models.AuditLog.entity_id == str(entity_id))
        .order_by(models.AuditLog.id)
    )
    return list(rows.scalars())


# --- create ------------------------------------------------------------------------


async def test_create_week_into_a_free_slot_and_it_is_listed(db_session: AsyncSession, season: int) -> None:
    await _free_last_slot(db_session, season)
    week = await content.create_week(
        db_session,
        actor_id=ADMIN_ID,
        season_id=season,
        number=13,
        starts_on=SLOT[0],
        ends_on=SLOT[1],
        today=TODAY,
        texts={"title": "Эпилог", "task_min": "Что забрал себе за сезон."},
    )
    assert week.number == 13 and week.title == "Эпилог" and week.task_max == "" and week.is_draft
    assert [w.number for w in await content.weeks(db_session, season)] == list(range(1, 12)), "a draft: not for people"
    assert [w.number for w in await content.weeks(db_session, season, include_drafts=True)] == [*range(1, 12), 13]
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
    await _free_last_slot(db_session, season)
    with pytest.raises(content.ContentError, match="already taken"):
        await content.create_week(
            db_session,
            actor_id=ADMIN_ID,
            season_id=season,
            number=2,
            starts_on=SLOT[0],
            ends_on=SLOT[1],
            today=TODAY,
        )
    # The refused inserts must not poison the session: the calendar is still usable.
    assert len(await content.weeks(db_session, season)) == 11


async def test_create_week_refuses_bad_dates_unknown_texts_and_the_next_season(
    db_session: AsyncSession, season: int
) -> None:
    with pytest.raises(ValueError, match="before it starts"):
        await content.create_week(
            db_session,
            actor_id=ADMIN_ID,
            season_id=season,
            number=13,
            starts_on=SLOT[1],
            ends_on=SLOT[0],
            today=TODAY,
        )
    with pytest.raises(ValueError, match="not editable"):
        await content.create_week(
            db_session,
            actor_id=ADMIN_ID,
            season_id=season,
            number=13,
            starts_on=SLOT[0],
            ends_on=SLOT[1],
            today=TODAY,
            texts={"starts_on": "2027-01-01"},
        )
    # 23.11 is the next country's first day (DOMAIN §1): outside this season, refused.
    with pytest.raises(content.ContentError, match="outside the season"):
        await content.create_week(
            db_session,
            actor_id=ADMIN_ID,
            season_id=season,
            number=13,
            starts_on=date(2026, 11, 23),
            ends_on=date(2026, 11, 29),
            today=TODAY,
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
        ends_on=date(2026, 11, 17),
    )
    assert (moved.starts_on, moved.ends_on, moved.number) == (date(2026, 11, 16), date(2026, 11, 17), 12)
    log = await _audit(db_session, week12.id)
    assert [row.action for row in log] == ["move"]
    assert log[0].before == {"ends_on": "2026-11-18"}, "only what changed is logged"
    assert log[0].after == {"ends_on": "2026-11-17"}
    with pytest.raises(content.ContentError, match="outside the season"):
        await content.move_week(
            db_session, actor_id=ADMIN_ID, week_id=week12.id, today=TODAY, ends_on=date(2026, 11, 19)
        )


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
    weeks = {w["number"]: w for w in (await app.client.get("/api/admin/weeks", headers=admin)).json()}
    assert (await app.client.delete(f"/api/admin/weeks/{weeks[12]['id']}", headers=admin)).status_code == 204

    r = await app.client.post(
        "/api/admin/weeks",
        json={"number": 13, "starts_on": "2026-11-16", "ends_on": "2026-11-18", "title": "Эпилог"},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["number"] == 13 and created["title"] == "Эпилог" and created["state"] == "locked"
    assert created["announced_at"] is None, "a new week is a draft"
    r = await app.client.post(f"/api/admin/weeks/{created['id']}/announce", headers=admin)
    assert r.status_code == 422 and "cannot be announced" in r.json()["detail"], "no minimum yet"
    r = await app.client.put(f"/api/admin/weeks/{created['id']}", json={"task_min": "Скажи слово."}, headers=admin)
    assert r.status_code == 200
    r = await app.client.post(f"/api/admin/weeks/{created['id']}/announce", headers=admin)
    assert r.status_code == 200 and r.json()["announced_at"] is not None

    r = await app.client.patch(
        f"/api/admin/weeks/{created['id']}/calendar", json={"ends_on": "2026-11-17", "number": 14}, headers=admin
    )
    assert r.status_code == 200, r.text
    assert r.json()["ends_on"] == "2026-11-17" and r.json()["number"] == 14

    r = await app.client.delete(f"/api/admin/weeks/{created['id']}", headers=admin)
    assert r.status_code == 204
    numbers = [w["number"] for w in (await app.client.get("/api/admin/weeks", headers=admin)).json()]
    assert numbers == list(range(1, 12))


async def test_admin_api_calendar_refusals_have_the_right_codes(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    weeks = (await app.client.get("/api/admin/weeks", headers=admin)).json()
    current, future = weeks[0], weeks[-1]

    r = await app.client.post(
        "/api/admin/weeks", json={"number": 2, "starts_on": "2026-11-23", "ends_on": "2026-11-29"}, headers=admin
    )
    assert r.status_code == 409 and "outside the season" in r.json()["detail"]

    r = await app.client.post(
        "/api/admin/weeks", json={"number": 13, "starts_on": "2026-11-18", "ends_on": "2026-11-16"}, headers=admin
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


# --- drafts: a week is invisible until Mila announces it; announcing is one way -----------


async def test_a_draft_week_is_invisible_not_current_and_not_a_miss(db_session: AsyncSession, season: int) -> None:
    """Release check, 14.09: an empty week became current, reminded about «задание «»» and
    cost every member a freeze. A new week is a draft until Mila announces it."""
    from romantika.services import passport

    await _free_last_slot(db_session, season)
    draft = await content.create_week(
        db_session,
        actor_id=ADMIN_ID,
        season_id=season,
        number=13,
        starts_on=SLOT[0],
        ends_on=SLOT[1],
        today=TODAY,
        texts={"title": "Эпилог", "task_min": "Скажи слово."},  # complete texts, still a draft
    )
    assert draft.is_draft and content.ready_to_announce(draft)

    listed = [w.number for w in await content.weeks(db_session, season)]
    assert 13 not in listed, "participants' list skips drafts"
    assert 13 in [w.number for w in await content.weeks(db_session, season, include_drafts=True)], "the admin sees it"

    on_its_day = date(2026, 11, 16)
    assert await content.current_week(db_session, season, today=on_its_day) is None, "a draft is never current"

    after = date(2026, 11, 19)  # the draft's dates are over; a real week would now be a miss
    view = await passport.build(db_session, season_id=season, user_id=ALICE, today=after)
    assert 13 not in view.breakdown.states and view.weeks_total == 11, "no freeze spent on a draft"

    announced = await content.announce_week(
        db_session, actor_id=ADMIN_ID, week_id=draft.id, now=moscow(2026, 9, 2, 12), season_id=season
    )
    assert not announced.is_draft
    assert (await content.current_week(db_session, season, today=on_its_day)) is not None
    assert 13 in [w.number for w in await content.weeks(db_session, season)]
    log = await _audit(db_session, draft.id)
    assert [row.action for row in log] == ["create", "announce"]


async def test_announce_needs_a_title_and_a_minimum(db_session: AsyncSession, season: int) -> None:
    await _free_last_slot(db_session, season)
    bare = await content.create_week(
        db_session, actor_id=ADMIN_ID, season_id=season, number=13, starts_on=SLOT[0], ends_on=SLOT[1], today=TODAY
    )
    with pytest.raises(ValueError, match="cannot be announced"):
        await content.announce_week(db_session, actor_id=ADMIN_ID, week_id=bare.id, now=moscow(2026, 9, 2, 12))
    with pytest.raises(ValueError, match="cannot be announced"):
        await content.create_week(
            db_session,
            actor_id=ADMIN_ID,
            season_id=season,
            number=14,
            starts_on=SLOT[0],
            ends_on=SLOT[1],
            today=TODAY,
            texts={"title": "Только название"},
            announce=moscow(2026, 9, 2, 12),
        )
    assert (await content.week_by_number(db_session, season, 13)) is not None and bare.is_draft


async def test_an_announced_week_never_falls_back_to_a_draft(db_session: AsyncSession, season: int) -> None:
    """Release check, 15.09 (critical): blanking the title of a week with stamps orphaned
    them and every passport answered 500. The title and the minimum of an announced week
    cannot be emptied; the week stays in every list with its stamps."""
    from romantika.services import passport, reports
    from romantika.services.reports import IncomingMessage

    week1 = await content.week_by_number(db_session, season, 1)
    assert week1 is not None and not week1.is_draft
    await reports.accept(
        db_session,
        season_id=season,
        user_id=ALICE,
        message=IncomingMessage(kind=ReportKind.TEXT, text="сделала", tg_chat_id=ALICE, tg_message_id=1),
        now=moscow(2026, 9, 2, 12),
    )
    for field_name in ("title", "task_min"):
        with pytest.raises(ValueError, match="cannot be emptied"):
            await content.update_week(
                db_session, actor_id=ADMIN_ID, week_id=week1.id, today=TODAY, changes={field_name: "  "}
            )
    # Other texts stay editable, and the passport keeps computing.
    await content.update_week(db_session, actor_id=ADMIN_ID, week_id=week1.id, today=TODAY, changes={"intro": ""})
    view = await passport.build(db_session, season_id=season, user_id=ALICE, today=TODAY)
    assert 1 in view.stamps and view.weeks_total == 12


async def test_no_stamp_and_no_intent_lands_on_a_draft(db_session: AsyncSession, season: int) -> None:
    """Release check, 15.09: a stamp set by Mila on a started draft, or an intent from a
    forged button, would be a row no passport can render."""
    from romantika.services import stamps

    await _free_last_slot(db_session, season)
    draft = await content.create_week(
        db_session,
        actor_id=ADMIN_ID,
        season_id=season,
        number=13,
        starts_on=SLOT[0],
        ends_on=SLOT[1],
        today=TODAY,
        texts={"title": "Эпилог", "task_min": "Скажи слово."},
    )
    with pytest.raises(content.ContentError, match="draft"):
        await stamps.admin_set(
            db_session,
            actor_id=ADMIN_ID,
            season_id=season,
            user_id=ALICE,
            week_number=13,
            level=StampLevel.MIN,
            now=moscow(2026, 11, 17, 12),  # the draft's dates have begun
        )
    assert draft.is_draft


async def test_weeks_are_ordered_by_calendar_not_by_number(db_session: AsyncSession, season: int) -> None:
    """Numbers are labels; the passport walks the season in date order."""
    from romantika.services import passport

    week11 = await content.week_by_number(db_session, season, 11)
    week12 = await content.week_by_number(db_session, season, 12)
    assert week11 is not None and week12 is not None
    await content.move_week(db_session, actor_id=ADMIN_ID, week_id=week11.id, today=TODAY, number=20)
    ordered = [w.number for w in await content.weeks(db_session, season)]
    assert ordered == [*range(1, 11), 20, 12], "ORDER BY starts_on, so the renumbered week keeps its place"
    view = await passport.build(db_session, season_id=season, user_id=ALICE, today=date(2026, 11, 17))
    assert view.breakdown.states[20] is not None and view.breakdown.states[12] is not None
    from romantika.services import journal

    grid = await journal.build(db_session, season_id=season, user_id=ALICE, today=date(2026, 11, 17))
    assert grid.week_numbers == ordered, "the PDF grid walks the same calendar order as the passport"


async def test_move_and_delete_are_scoped_to_the_active_season(db_session: AsyncSession, season: int) -> None:
    """A guessable id of a next-season week must not be editable from this season's admin."""
    other = models.Season(
        slug="japan-2027",
        title="Япония",
        title_accusative="Японию",
        hashtag="#япония",
        starts_on=date(2026, 11, 23),
        ends_on=date(2027, 2, 21),
        status=models.SeasonStatus.DRAFT.value,
        daily_kind=None,
        daily_title="",
        daily_note="",
        base_freezes=2,
        max_freezes=5,
        level_tourist=3,
        level_traveler=6,
        level_resident=9,
    )
    db_session.add(other)
    await db_session.flush()
    foreign = await content.create_week(
        db_session,
        actor_id=ADMIN_ID,
        season_id=other.id,
        number=1,
        starts_on=date(2026, 11, 23),
        ends_on=date(2026, 11, 29),
        today=TODAY,
    )
    with pytest.raises(content.ContentError, match="does not exist"):
        await content.delete_week(db_session, actor_id=ADMIN_ID, week_id=foreign.id, today=TODAY, season_id=season)
    with pytest.raises(content.ContentError, match="does not exist"):
        await content.move_week(
            db_session, actor_id=ADMIN_ID, week_id=foreign.id, today=TODAY, season_id=season, number=2
        )
    assert await content.week_by_number(db_session, other.id, 1) is not None


async def test_a_hidden_fact_still_pins_its_week(db_session: AsyncSession, season: int) -> None:
    """Nothing is ever deleted (CLAUDE.md rule 1): a fact Mila hid still points at the week,
    so the week stays — with a reason, not a 500."""
    from romantika.services import facts

    week12 = await content.week_by_number(db_session, season, 12)
    assert week12 is not None
    fact_id = await facts.add(
        db_session,
        season_id=season,
        week_id=week12.id,
        text="Сомбреро — не только шляпа.",
        author_id=ADMIN_ID,
        now=moscow(2026, 9, 2, 12),
    )
    assert await facts.remove(db_session, fact_id=fact_id, actor_id=ADMIN_ID, now=moscow(2026, 9, 2, 13))
    with pytest.raises(content.ContentError, match="participant data"):
        await content.delete_week(db_session, actor_id=ADMIN_ID, week_id=week12.id, today=TODAY)
    assert await content.week_by_number(db_session, season, 12) is not None
