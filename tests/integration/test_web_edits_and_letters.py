"""What the second Mini App round added (spec v2, defects D1–D8 and the accepted defaults):
idempotent submissions, editing a report while its week is open, Mila's inbox of letters,
the week state Mila sees, reminders for a named week, the people filters' week columns."""

# ruff: noqa: F811 — the `app` fixture is imported from the sibling module on purpose
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.domain.types import ReportKind
from romantika.services import content, letters, reports
from romantika.services.reports import IncomingMessage
from tests.integration.test_web_miniapp import ADMIN_ID, ALICE, BOB, JPEG, App, app, moscow  # noqa: F401

# --- idempotent submission (D6) ----------------------------------------------------


async def test_a_retry_with_the_same_client_id_returns_the_same_report(app: App) -> None:
    payload = {"text": "энчилада", "client_id": "c-1"}
    first = await app.client.post("/api/reports", data=payload, headers=app.headers(ALICE))
    second = await app.client.post("/api/reports", data=payload, headers=app.headers(ALICE))
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["report_id"] == second.json()["report_id"]
    assert second.json()["message"] == first.json()["message"]
    rows = list((await app.session.execute(select(models.Report))).scalars())
    assert len(rows) == 1 and rows[0].client_id == "c-1"
    assert len(await app.jobs("telegram_notify")) == 2, "Mila and the author were told once"
    # another person may reuse the id: the key is (user, client_id)
    r = await app.client.post("/api/reports", data=payload, headers=app.headers(BOB, "Боб"))
    assert r.status_code == 201 and r.json()["report_id"] != first.json()["report_id"]


# --- editing (accepted default: while the week is open) ------------------------------


async def test_edit_changes_text_adds_a_photo_and_earns_the_star(app: App) -> None:
    created = (await app.client.post("/api/reports", data={"text": "раз"}, headers=app.headers(ALICE))).json()
    report_id = created["report_id"]
    assert await app.stamp(ALICE, 1) == "min"
    journal = (await app.client.get("/api/journal", headers=app.headers(ALICE))).json()
    assert journal["reports"][0]["editable"] is True and journal["reports"][0]["edited_at"] is None

    r = await app.client.patch(
        f"/api/reports/{report_id}",
        data={"text": "раз, и вот фото"},
        files=[("files", ("photo.jpg", JPEG, "image/jpeg"))],
        headers=app.headers(ALICE),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stamp_level"] == "max" and body["freeze_granted"] is True
    assert body["report"]["text"] == "раз, и вот фото" and body["report"]["level"] == "max"
    assert body["report"]["edited_at"] is not None and len(body["report"]["media"]) == 1
    assert "звёздочкой" in body["message"]
    assert await app.stamp(ALICE, 1) == "max"

    photo = (await app.session.execute(select(models.Media))).scalar_one()
    assert app.store.full_path(photo.path).read_bytes() == JPEG
    jobs = await app.jobs("telegram_notify")
    copy = [j for j in jobs if j.payload["chat_id"] == ADMIN_ID][-1]
    assert "Правка отчёта за неделю 1 от Алиса" in copy.payload["text"] and "+1 файл" in copy.payload["text"]
    assert copy.payload["media_ids"] == [str(photo.id)] and copy.payload["link"]["report_id"] == report_id
    receipt = [j for j in jobs if j.payload["chat_id"] == ALICE][-1]
    assert "обновила" in receipt.payload["text"]
    audit = (await app.session.execute(select(models.AuditLog).where(models.AuditLog.action == "edit"))).scalar_one()
    assert audit.actor_id == ALICE and audit.entity_id == str(report_id)
    assert audit.before == {"text": "раз"} and audit.after["text"] == "раз, и вот фото" and audit.after["added"] == 1


async def test_removing_the_only_photo_recomputes_the_stamp_down(app: App) -> None:
    created = (
        await app.client.post(
            "/api/reports",
            data={"text": "с фото"},
            files=[("files", ("photo.jpg", JPEG, "image/jpeg"))],
            headers=app.headers(ALICE),
        )
    ).json()
    assert await app.stamp(ALICE, 1) == "max"
    media_id = str((await app.session.execute(select(models.Media))).scalar_one().id)

    r = await app.client.patch(
        f"/api/reports/{created['report_id']}",
        data={"text": "с фото", "remove": [media_id]},
        headers=app.headers(ALICE),
    )
    assert r.status_code == 200, r.text
    assert r.json()["stamp_level"] == "min" and r.json()["report"]["media"] == []
    assert "минимум" in r.json()["message"]
    assert await app.stamp(ALICE, 1) == "min"
    row = (await app.session.execute(select(models.Media))).scalar_one()
    assert row.hidden_at is not None and app.store.full_path(row.path).exists(), "hidden, never deleted"
    copy = [j for j in await app.jobs("telegram_notify") if j.payload["chat_id"] == ADMIN_ID][-1]
    assert "−1 файл" in copy.payload["text"]

    # nothing left → refused, the report stays as it was
    r = await app.client.patch(f"/api/reports/{created['report_id']}", data={"text": "   "}, headers=app.headers(ALICE))
    assert r.status_code == 422
    assert await app.stamp(ALICE, 1) == "min"


async def test_edit_is_refused_for_foreign_cancelled_and_overfull_reports(app: App) -> None:
    created = (await app.client.post("/api/reports", data={"text": "раз"}, headers=app.headers(ALICE))).json()
    report_id = created["report_id"]
    assert (
        await app.client.patch(f"/api/reports/{report_id}", data={"text": "чужое"}, headers=app.headers(BOB, "Боб"))
    ).status_code == 403
    too_many = [("files", (f"{i}.jpg", JPEG, "image/jpeg")) for i in range(11)]
    assert (
        await app.client.patch(f"/api/reports/{report_id}", files=too_many, headers=app.headers(ALICE))
    ).status_code == 413
    assert (await app.client.post(f"/api/reports/{report_id}/cancel", headers=app.headers(ALICE))).status_code == 200
    r = await app.client.patch(f"/api/reports/{report_id}", data={"text": "поздно"}, headers=app.headers(ALICE))
    assert r.status_code == 409 and "уже отменён" in r.json()["detail"]


async def test_edit_after_the_week_is_over_is_refused(app: App) -> None:
    created = (await app.client.post("/api/reports", data={"text": "раз"}, headers=app.headers(ALICE))).json()
    # the service decides by «now»; the route cannot move the clock, so ask the service directly
    later = moscow(2026, 9, 7, 9)  # Monday after week 1 (ends Sunday 06.09)
    result = await reports.edit(
        app.session,
        user_id=ALICE,
        report_id=created["report_id"],
        text="задним числом",
        new_files=[],
        remove_media_ids=[],
        now=later,
    )
    assert result.ok is False and result.reason == reports.WEEK_OVER
    row = await app.session.get(models.Report, created["report_id"])
    assert row is not None and row.text == "раз" and row.edited_at is None
    assert reports.editable_until(date(2026, 9, 6), later.date()) is False
    assert reports.editable_until(date(2026, 9, 6), date(2026, 9, 6)) is True, "the last day still counts"
    assert reports.editable_until(None, later.date()) is False, "a letter has no week"


# --- letters: Mila's inbox -----------------------------------------------------------


async def test_letters_from_the_app_and_a_taken_back_report_land_in_the_inbox(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    r = await app.client.post("/api/letters", json={"text": "Мила, я уезжаю"}, headers=app.headers(ALICE))
    assert r.status_code == 200, r.text
    created = (await app.client.post("/api/reports", data={"text": "ой не то"}, headers=app.headers(ALICE))).json()
    await app.client.post(f"/api/reports/{created['report_id']}/cancel", headers=app.headers(ALICE))

    inbox = (await app.client.get("/api/admin/letters", headers=admin)).json()
    assert inbox["unanswered"] == 2
    assert [item["source"] for item in inbox["letters"]] == ["not_report", "app"]
    assert all(item["author"].startswith("Алиса") for item in inbox["letters"])
    taken_back, letter = inbox["letters"]
    assert taken_back["report_id"] == created["report_id"] and "ой не то" in taken_back["text"]
    assert letter["reply_text"] is None and letter["text"] == "Мила, я уезжаю"
    assert (await app.client.get("/api/admin/letters", headers=app.headers(ALICE))).status_code == 403

    r = await app.client.post(
        f"/api/admin/letters/{letter['id']}/reply", json={"text": "Хорошей дороги!"}, headers=admin
    )
    assert r.status_code == 202
    note = [j for j in await app.jobs("telegram_notify") if j.payload["chat_id"] == ALICE][-1]
    assert "Мила ответила" in note.payload["text"] and "Хорошей дороги!" in note.payload["text"]
    inbox = (await app.client.get("/api/admin/letters", headers=admin)).json()
    assert inbox["unanswered"] == 1
    answered = next(item for item in inbox["letters"] if item["id"] == letter["id"])
    assert answered["reply_text"] == "Хорошей дороги!" and answered["replied_at"] is not None
    assert (
        await app.client.post("/api/admin/letters/424242/reply", json={"text": "x"}, headers=admin)
    ).status_code == 404
    # the admin copy carries the letter id, so a chat reply marks the same row
    copy = next(j for j in await app.jobs("telegram_notify") if j.payload["chat_id"] == ADMIN_ID)
    assert copy.payload["link"]["letter_id"] == letter["id"]


async def test_out_of_week_message_becomes_a_letter(db_session: AsyncSession, app: App) -> None:
    before = moscow(2026, 8, 25, 12)  # season not started: no week running
    result = await reports.accept(
        db_session,
        season_id=app.season_id,
        user_id=ALICE,
        message=IncomingMessage(kind=ReportKind.TEXT, text="рано пришёл"),
        now=before,
    )
    assert result.out_of_week is True
    row = await letters.create(
        db_session, season_id=app.season_id, user_id=ALICE, source=letters.Source.OUT_OF_WEEK,
        text="рано пришёл", report_id=result.report_id, now=before,
    )  # fmt: skip
    listed = await letters.list_for_season(db_session, app.season_id)
    assert [item.source for item in listed] == [letters.Source.OUT_OF_WEEK] and listed[0].id == row.id
    assert await letters.unanswered_count(db_session, app.season_id) == 1
    assert await letters.get(db_session, 424242) is None


# --- admin: week state (D2), reminders for a week (D1), people columns (D8) -----------


async def test_admin_week_state_survives_an_edit(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    weeks = (await app.client.get("/api/admin/weeks", headers=admin)).json()
    assert [w["state"] for w in weeks[:3]] == ["current", "locked", "locked"]
    first = weeks[0]
    r = await app.client.put(f"/api/admin/weeks/{first['id']}", json={"intro": "новое интро"}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "current" and r.json()["intro"] == "новое интро"
    assert r.json()["number"] == 1 and r.json()["ends_on"] == "2026-09-06"


async def test_remind_now_carries_the_week_and_refuses_unknown_or_past_weeks(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    assert (await app.client.post("/api/admin/remind", json={"week_number": 99}, headers=admin)).status_code == 404
    r = await app.client.post("/api/admin/remind", json={"week_number": 2}, headers=admin)
    assert r.status_code == 409 and "не началась" in r.json()["detail"], "a week nobody has seen yet"
    r = await app.client.post("/api/admin/remind", json={"week_number": 1}, headers=admin)
    assert r.status_code == 202
    (job,) = await app.jobs("reminders_now")
    assert job.payload["week_number"] == 1 and job.payload["requested_by"] == ADMIN_ID
    week = await content.week_by_number(app.session, app.season_id, 2)
    assert week is not None
    row = await app.session.get(models.Week, week.id)
    assert row is not None
    row.starts_on, row.ends_on = date(2026, 8, 24), date(2026, 8, 30)  # pretend week 2 is already over
    await app.session.flush()
    r = await app.client.post("/api/admin/remind", json={"week_number": 2}, headers=admin)
    assert r.status_code == 409 and "прошла" in r.json()["detail"]


async def test_participants_show_the_intent_and_the_stamp_of_the_running_week(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    await app.client.post("/api/intent", json={"week_number": 1, "choice": "take"}, headers=app.headers(ALICE))
    await app.client.post("/api/reports", data={"text": "раз"}, headers=app.headers(BOB, "Боб"))
    people = {p["id"]: p for p in (await app.client.get("/api/admin/participants", headers=admin)).json()}
    assert people[ALICE]["week_intent"] == "take" and people[ALICE]["week_level"] is None
    assert people[BOB]["week_intent"] is None and people[BOB]["week_level"] == "min"
