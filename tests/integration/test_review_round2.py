"""What the second review round found and the code now guarantees (code review + API QA):
a stamp Mila removed stays removed, uploads are refused before they are read, nothing stays
on disk after a failed upload, edits are idempotent, files never run as pages, blank texts
are refused, a future week takes neither intents nor stamps, and the admin app tells the
participant what the bot would."""

# ruff: noqa: F811 — the `app` fixture is imported from the sibling module on purpose
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from romantika.db import models
from romantika.services import reminders, stamps
from romantika.web.routes import api as api_routes
from tests.integration.test_web_miniapp import ADMIN_ID, ALICE, BOB, JPEG, App, app, moscow  # noqa: F401

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'


# --- a stamp Mila removed does not come back through an edit ---------------------------------


async def test_editing_after_mila_removed_the_stamp_does_not_bring_it_back(app: App) -> None:
    created = (await app.client.post("/api/reports", data={"text": "раз"}, headers=app.headers(ALICE))).json()
    assert await app.stamp(ALICE, 1) == "min"
    admin = app.headers(ADMIN_ID, "Мила")
    r = await app.client.put(f"/api/admin/participants/{ALICE}/stamps/1", json={"level": None}, headers=admin)
    assert r.status_code == 200 and await app.stamp(ALICE, 1) is None

    r = await app.client.patch(
        f"/api/reports/{created['report_id']}",
        data={"text": "раз, теперь с фото"},
        files=[("files", ("p.jpg", JPEG, "image/jpeg"))],
        headers=app.headers(ALICE),
    )
    assert r.status_code == 200 and r.json()["stamp_level"] is None, "Mila's decision stands"
    assert await app.stamp(ALICE, 1) is None

    # A new report, sent after her decision, earns the week as usual.
    r = await app.client.post("/api/reports", data={"text": "новый отчёт"}, headers=app.headers(ALICE))
    assert r.status_code == 201 and await app.stamp(ALICE, 1) == "min"
    # …and cancelling the old one leaves the new one's stamp alone.
    r = await app.client.post(f"/api/reports/{created['report_id']}/cancel", headers=app.headers(ALICE))
    assert r.status_code == 200 and await app.stamp(ALICE, 1) == "min"


# --- uploads: refused before they are read, cleaned up when they fail ------------------------


async def test_anonymous_upload_is_refused_and_an_oversized_request_is_refused_by_its_header(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = await app.client.post("/api/reports", files=[("files", ("p.jpg", JPEG, "image/jpeg"))])
    assert r.status_code == 401
    monkeypatch.setattr(api_routes, "MAX_REQUEST_BYTES", 64)
    r = await app.client.post(
        "/api/reports", files=[("files", ("p.jpg", JPEG, "image/jpeg"))], headers=app.headers(ALICE)
    )
    assert r.status_code == 413 and "вместе" in r.json()["detail"]
    assert not [p for p in app.store.root.rglob("*") if p.is_file()]


async def test_a_failed_second_file_leaves_no_trace_of_the_first(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_routes, "MAX_UPLOAD_BYTES", 64)
    r = await app.client.post(
        "/api/reports",
        files=[("files", ("small.jpg", JPEG[:32], "image/jpeg")), ("files", ("big.jpg", JPEG, "image/jpeg"))],
        headers=app.headers(ALICE),
    )
    assert r.status_code == 413
    assert not [p for p in app.store.root.rglob("*") if p.is_file()], "the first file was removed with the rollback"
    assert (await app.session.execute(select(models.Report))).scalars().first() is None


async def test_an_empty_file_is_no_file(app: App) -> None:
    r = await app.client.post(
        "/api/reports", files=[("files", ("empty.txt", b"", "text/plain"))], headers=app.headers(ALICE)
    )
    assert r.status_code == 422, "no text and a zero-byte file is an empty report, not a maximum"
    r = await app.client.post(
        "/api/reports",
        data={"text": "слова"},
        files=[("files", ("empty.txt", b"", "text/plain"))],
        headers=app.headers(ALICE),
    )
    assert r.status_code == 422, "next to a text it is refused too, not dropped in silence"


async def test_a_report_text_has_a_limit(app: App) -> None:
    r = await app.client.post("/api/reports", data={"text": "я" * 4001}, headers=app.headers(ALICE))
    assert r.status_code == 422


# --- editing: idempotent on the client's key, stable media order ---------------------------------


async def test_a_retried_edit_with_the_same_key_adds_the_file_once(app: App) -> None:
    created = (await app.client.post("/api/reports", data={"text": "раз"}, headers=app.headers(ALICE))).json()
    report_id = created["report_id"]
    payload = {"text": "раз и фото", "edit_key": "e-1"}
    first = await app.client.patch(
        f"/api/reports/{report_id}",
        data=payload,
        files=[("files", ("p.jpg", JPEG, "image/jpeg"))],
        headers=app.headers(ALICE),
    )
    second = await app.client.patch(
        f"/api/reports/{report_id}",
        data=payload,
        files=[("files", ("p.jpg", JPEG, "image/jpeg"))],
        headers=app.headers(ALICE),
    )
    assert first.status_code == 200 and second.status_code == 200
    assert len(second.json()["report"]["media"]) == 1 and "обновила" in second.json()["message"]
    media = list((await app.session.execute(select(models.Media))).scalars())
    assert len(media) == 1
    copies = [j for j in await app.jobs("telegram_notify") if "Правка отчёта" in (j.payload["text"] or "")]
    assert len(copies) == 1, "Mila hears about the edit once"


async def test_media_keep_their_order_after_an_edit(app: App) -> None:
    created = (
        await app.client.post(
            "/api/reports", files=[("files", ("p.jpg", JPEG, "image/jpeg"))], headers=app.headers(ALICE)
        )
    ).json()
    report_id = created["report_id"]
    for name, mime in (("a.pdf", "application/pdf"), ("b.pdf", "application/pdf")):
        r = await app.client.patch(
            f"/api/reports/{report_id}",
            data={"text": ""},
            files=[("files", (name, b"%PDF-1.4 " + JPEG, mime))],
            headers=app.headers(ALICE),
        )
        assert r.status_code == 200
    report = r.json()["report"]
    assert report["kind"] == "photo", "the first file still names the report's kind"
    assert [m["mime"] for m in report["media"]] == ["image/jpeg", "application/pdf", "application/pdf"]


async def test_editing_someone_elses_report_is_forbidden_before_any_limit_is_counted(app: App) -> None:
    created = (await app.client.post("/api/reports", data={"text": "раз"}, headers=app.headers(ALICE))).json()
    files = [("files", (f"{i}.jpg", JPEG, "image/jpeg")) for i in range(api_routes.MAX_UPLOAD_FILES + 1)]
    r = await app.client.patch(
        f"/api/reports/{created['report_id']}", data={"text": "x"}, files=files, headers=app.headers(BOB, "Боб")
    )
    assert r.status_code == 403


# --- an out-of-week message taken back is still one letter ----------------------------------------


async def test_taking_back_an_out_of_week_message_makes_no_second_letter(app: App) -> None:
    for number in (1, 2, 3):
        week = await app.session.execute(
            select(models.Week).where(models.Week.season_id == app.season_id, models.Week.number == number)
        )
        row = week.scalar_one()
        row.starts_on = date(2026, 7, 6) + timedelta(days=7 * number)
        row.ends_on = row.starts_on + timedelta(days=6)
    await app.session.flush()
    created = (await app.client.post("/api/reports", data={"text": "между"}, headers=app.headers(ALICE))).json()
    assert created["out_of_week"] is True
    r = await app.client.post(f"/api/reports/{created['report_id']}/cancel", headers=app.headers(ALICE))
    assert r.status_code == 200
    rows = list((await app.session.execute(select(models.Letter))).scalars())
    assert len(rows) == 1 and rows[0].report_id == created["report_id"]
    inbox = (await app.client.get("/api/admin/letters", headers=app.headers(ADMIN_ID, "Мила"))).json()
    assert inbox["unanswered"] == 1


# --- files never run as pages; hidden files stay with Mila -----------------------------------------


async def test_a_participants_svg_is_served_as_a_download_and_a_photo_inline(app: App) -> None:
    created = (
        await app.client.post(
            "/api/reports",
            files=[("files", ("x.svg", SVG, "image/svg+xml")), ("files", ("p.jpg", JPEG, "image/jpeg"))],
            headers=app.headers(ALICE),
        )
    ).json()
    journal = (await app.client.get("/api/journal", headers=app.headers(ALICE))).json()
    (report,) = [r for r in journal["reports"] if r["id"] == created["report_id"]]
    svg, jpg = report["media"]
    r = await app.client.get(svg["url"], headers=app.headers(ALICE))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"
    r = await app.client.get(jpg["url"], headers=app.headers(ALICE))
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "content-disposition" not in r.headers


async def test_a_hidden_file_is_gone_for_the_owner_but_not_for_mila(app: App) -> None:
    created = (
        await app.client.post(
            "/api/reports",
            data={"text": "с фото"},
            files=[("files", ("p.jpg", JPEG, "image/jpeg"))],
            headers=app.headers(ALICE),
        )
    ).json()
    journal = (await app.client.get("/api/journal", headers=app.headers(ALICE))).json()
    (report,) = [r for r in journal["reports"] if r["id"] == created["report_id"]]
    media = report["media"][0]
    r = await app.client.patch(
        f"/api/reports/{created['report_id']}",
        data={"text": "без фото", "remove": media["id"]},
        headers=app.headers(ALICE),
    )
    assert r.status_code == 200 and r.json()["report"]["media"] == []
    assert (await app.client.get(media["url"], headers=app.headers(ALICE))).status_code == 404
    assert (await app.client.get(media["url"], headers=app.headers(ADMIN_ID, "Мила"))).status_code == 200


# --- blank texts are refused everywhere ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/words", {"text": "   "}),
        ("POST", "/api/facts", {"text": "   "}),
        ("POST", "/api/letters", {"text": " \n "}),
    ],
)
async def test_blank_participant_texts_are_422(app: App, method: str, path: str, body: dict[str, str]) -> None:
    r = await app.client.request(method, path, json=body, headers=app.headers(ALICE))
    assert r.status_code == 422, r.text
    assert (await app.session.execute(select(models.Letter))).scalars().first() is None


async def test_blank_admin_texts_are_422(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    assert (
        await app.client.put(f"/api/admin/participants/{ALICE}/wish", json={"text": "  "}, headers=admin)
    ).status_code == 422
    assert (await app.client.post("/api/admin/facts", json={"text": " "}, headers=admin)).status_code == 422
    r = await app.client.post(
        f"/api/admin/participants/{ALICE}/achievements", json={"code_or_text": " "}, headers=admin
    )
    assert r.status_code == 422
    r = await app.client.post(f"/api/admin/participants/{ALICE}/message", json={"text": " "}, headers=admin)
    assert r.status_code == 422
    assert await app.jobs("telegram_notify") == []


async def test_a_service_refusal_is_a_422_not_a_500(app: App) -> None:
    r = await app.client.post("/api/words", json={"text": ": только значение"}, headers=app.headers(ALICE))
    assert r.status_code == 422 and r.json()["detail"]


# --- a future week takes neither intents nor stamps --------------------------------------------------


async def test_a_future_week_takes_no_intent_and_no_stamp(app: App) -> None:
    r = await app.client.post("/api/intent", json={"week_number": 5, "choice": "take"}, headers=app.headers(ALICE))
    assert r.status_code == 409
    assert await app.jobs("telegram_notify") == [], "Mila is not told about a guess"
    admin = app.headers(ADMIN_ID, "Мила")
    r = await app.client.put(f"/api/admin/participants/{ALICE}/stamps/3", json={"level": "max"}, headers=admin)
    assert r.status_code == 409 and "not started" in r.json()["detail"]
    assert await app.stamp(ALICE, 3) is None
    r = await app.client.post("/api/weeks/99/level", json={"level": "max"}, headers=app.headers(ALICE))
    assert r.status_code == 404
    r = await app.client.put("/api/admin/weeks/999", json={"intro": "x"}, headers=admin)
    assert r.status_code == 404


# --- the admin app tells the participant what the bot would ------------------------------------------


async def test_achievement_and_freeze_from_the_admin_app_reach_the_participant(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    r = await app.client.post(
        f"/api/admin/participants/{ALICE}/achievements", json={"code_or_text": "повар"}, headers=admin
    )
    assert r.status_code == 201 and r.json()["created"] is True
    again = await app.client.post(
        f"/api/admin/participants/{ALICE}/achievements", json={"code_or_text": "повар"}, headers=admin
    )
    assert again.status_code == 200 and again.json()["created"] is False
    r = await app.client.post(f"/api/admin/participants/{ALICE}/freezes", json={"reason": "meetup"}, headers=admin)
    assert r.status_code == 201 and r.json()["granted"] is True
    jobs = await app.jobs("telegram_notify")
    texts = [j.payload["text"] for j in jobs if j.payload["chat_id"] == ALICE]
    assert len(texts) == 2 and "ачивка" in texts[0] and "+1 заморозка" in texts[1]


async def test_a_reply_to_a_letter_is_answered_as_a_letter(app: App) -> None:
    await app.client.post("/api/letters", json={"text": "вопрос"}, headers=app.headers(ALICE))
    admin = app.headers(ADMIN_ID, "Мила")
    inbox = (await app.client.get("/api/admin/letters", headers=admin)).json()
    letter_id = inbox["letters"][0]["id"]
    r = await app.client.post(f"/api/admin/letters/{letter_id}/reply", json={"text": "ответ"}, headers=admin)
    assert r.status_code == 202
    note = [j for j in await app.jobs("telegram_notify") if j.payload["chat_id"] == ALICE][-1]
    assert "на твоё сообщение" in note.payload["text"]


async def test_timestamps_leave_the_api_as_utc(app: App) -> None:
    created = (await app.client.post("/api/reports", data={"text": "раз"}, headers=app.headers(ALICE))).json()
    r = await app.client.patch(f"/api/reports/{created['report_id']}", data={"text": "два"}, headers=app.headers(ALICE))
    report = r.json()["report"]
    assert report["created_at"].endswith("Z") and report["edited_at"].endswith("Z")


# --- reminders pick the text of the day ---------------------------------------------------------------


async def test_manual_reminder_uses_the_sunday_text_on_the_last_day(app: App) -> None:
    class Gateway:
        def __init__(self) -> None:
            self.texts: list[tuple[int, str]] = []

        async def send_message(self, chat_id: int, text: str) -> None:
            self.texts.append((chat_id, text))

    await app.client.post("/api/intent", json={"week_number": 1, "choice": "take"}, headers=app.headers(ALICE))
    gateway = Gateway()
    await reminders.send(
        app.session, season_id=app.season_id, week_number=1, telegram=gateway, now=moscow(2026, 9, 3, 19)
    )
    await reminders.send(
        app.session, season_id=app.season_id, week_number=1, telegram=gateway, now=moscow(2026, 9, 6, 12)
    )
    assert "Впереди выходные" in gateway.texts[0][1] and "Сегодня до 18:00" in gateway.texts[1][1]


async def test_cleared_reports_are_remembered_with_the_removal(app: App) -> None:
    created = (await app.client.post("/api/reports", data={"text": "раз"}, headers=app.headers(ALICE))).json()
    admin = app.headers(ADMIN_ID, "Мила")
    await app.client.put(f"/api/admin/participants/{ALICE}/stamps/1", json={"level": None}, headers=admin)
    week = (await app.client.get("/api/home", headers=app.headers(ALICE))).json()["week"]
    week_row = await app.session.execute(select(models.Week).where(models.Week.number == week["number"]))
    cleared = await stamps.cleared_reports(app.session, user_id=ALICE, week_id=week_row.scalar_one().id)
    assert cleared == {created["report_id"]}


# --- round three: what the UI testers found -----------------------------------------------------


async def test_a_text_after_a_photo_says_the_star_stays(app: App) -> None:
    await app.client.post("/api/reports", files=[("files", ("p.jpg", JPEG, "image/jpeg"))], headers=app.headers(ALICE))
    r = (await app.client.post("/api/reports", data={"text": "и пара слов"}, headers=app.headers(ALICE))).json()
    assert (r["level"], r["stamp_level"]) == ("min", "max")
    assert "Записала как <b>минимум</b>" in r["message"] and "остаётся" in r["message"]
    assert "со звёздочкой" not in r["message"], "the receipt does not present a text as a maximum"


async def test_the_same_word_twice_is_one_entry(app: App) -> None:
    first = await app.client.post("/api/words", json={"text": "chido — классный"}, headers=app.headers(ALICE))
    assert first.status_code == 201
    again = await app.client.post("/api/words", json={"text": "Chido: клёвый"}, headers=app.headers(ALICE))
    assert again.status_code == 422 and "уже есть" in again.json()["detail"]
    words = (await app.client.get("/api/dictionary", headers=app.headers(ALICE))).json()
    assert [w["word"] for w in words["user_words"]] == ["chido"]


async def test_a_letter_that_came_as_a_photo_shows_its_file_to_mila(app: App) -> None:
    created = (
        await app.client.post(
            "/api/reports", files=[("files", ("p.jpg", JPEG, "image/jpeg"))], headers=app.headers(ALICE)
        )
    ).json()
    await app.client.post(f"/api/reports/{created['report_id']}/cancel", headers=app.headers(ALICE))
    inbox = (await app.client.get("/api/admin/letters", headers=app.headers(ADMIN_ID, "Мила"))).json()
    (letter,) = inbox["letters"]
    assert letter["report_id"] == created["report_id"]
    assert [m["mime"] for m in letter["media"]] == ["image/jpeg"]


async def test_the_audit_log_names_who_did_it(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    await app.client.put(f"/api/admin/participants/{ALICE}/stamps/1", json={"level": "max"}, headers=admin)
    rows = (await app.client.get("/api/admin/audit", headers=admin)).json()
    assert rows[0]["action"] == "set" and rows[0]["entity"] == "stamp"
    assert rows[0]["actor_name"] == "Мила"


async def test_a_future_week_has_no_draft_yet(app: App) -> None:
    s = (await app.client.get("/api/admin/summary?week=5", headers=app.headers(ADMIN_ID, "Мила"))).json()
    assert s["draft_post"] == ""
    assert any("ещё не началась" in note for note in s["draft_notes"])


# --- round three: what the API critic found ------------------------------------------------------


async def test_a_stamp_mila_removed_stays_removed_even_after_a_later_report_is_cancelled(app: App) -> None:
    """Once a later report has earned the week again, cancelling it must not fall back on the
    reports Mila had in front of her when she took the stamp away (the critic's B-1)."""
    admin = app.headers(ADMIN_ID, "Мила")
    photo = (
        await app.client.post(
            "/api/reports", files=[("files", ("p.jpg", JPEG, "image/jpeg"))], headers=app.headers(ALICE)
        )
    ).json()
    assert photo["stamp_level"] == "max"
    r = await app.client.put(f"/api/admin/participants/{ALICE}/stamps/1", json={"level": None}, headers=admin)
    assert r.status_code == 200
    later = (await app.client.post("/api/reports", data={"text": "новый"}, headers=app.headers(ALICE))).json()
    assert later["stamp_level"] == "min", "a report sent after her decision earns the week again"
    r = await app.client.post(f"/api/reports/{later['report_id']}/cancel", headers=app.headers(ALICE))
    assert r.status_code == 200 and r.json()["stamp_level"] is None
    week = await app.session.execute(
        select(models.Week).where(models.Week.season_id == app.season_id, models.Week.number == 1)
    )
    assert await stamps.get_level(app.session, user_id=ALICE, week_id=week.scalar_one().id) is None
    r = await app.client.post(f"/api/reports/{photo['report_id']}/cancel", headers=app.headers(ALICE))
    assert r.status_code == 200 and r.json()["stamp_level"] is None


async def test_the_pdf_footer_keeps_its_quotes(app: App) -> None:
    from romantika.pdf.journal import render_journal_html
    from romantika.services import journal

    await app.client.post("/api/reports", data={"text": "строка"}, headers=app.headers(ALICE))
    view = await journal.build(app.session, season_id=app.season_id, user_id=ALICE, today=app.now.date())
    html = render_journal_html(view)
    style = html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert f'content: "Романтика маршрутов · " "{view.season.title}"' in style
    assert "&#34;" not in style and "&quot;" not in style, "HTML escaping inside <style> kills the footer"


async def test_a_nul_byte_is_refused_not_a_500(app: App) -> None:
    r = await app.client.post("/api/letters", json={"text": "привет\x00мир"}, headers=app.headers(ALICE))
    assert r.status_code == 422
    r = await app.client.post("/api/reports", data={"text": "тест\x00нуль"}, headers=app.headers(ALICE))
    assert r.status_code == 422 and "символы" in r.json()["detail"]


async def test_an_overlong_attempt_key_is_refused_not_cut(app: App) -> None:
    long_key = "c" * 64
    r = await app.client.post(
        "/api/reports", data={"text": "первый", "client_id": long_key + "AAA"}, headers=app.headers(ALICE)
    )
    assert r.status_code == 422
    r = await app.client.post(
        "/api/reports", data={"text": "первый", "client_id": long_key}, headers=app.headers(ALICE)
    )
    assert r.status_code == 201


async def test_editing_an_unknown_report_is_a_404(app: App) -> None:
    r = await app.client.patch("/api/reports/999999", data={"text": "x"}, headers=app.headers(ALICE))
    assert r.status_code == 404


async def test_the_draft_quotes_one_line_per_person(app: App) -> None:
    await app.client.post("/api/reports", data={"text": "Строка 1\nСтрока 2\n\nконец"}, headers=app.headers(ALICE))
    s = (await app.client.get("/api/admin/summary?week=1", headers=app.headers(ADMIN_ID, "Мила"))).json()
    assert "Строка 1" in s["draft_post"] and "Строка 2" not in s["draft_post"]


async def test_a_fact_for_an_unknown_week_is_a_404(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    r = await app.client.post("/api/admin/facts", json={"text": "факт", "week_number": 999}, headers=admin)
    assert r.status_code == 404


async def test_a_file_under_another_field_name_is_not_an_attachment(app: App) -> None:
    r = await app.client.post(
        "/api/reports",
        data={"text": "только текст"},
        files=[("junk", ("p.jpg", JPEG, "image/jpeg"))],
        headers=app.headers(ALICE),
    )
    assert r.status_code == 201 and r.json()["level"] == "min"


async def test_a_retried_edit_answers_with_a_receipt(app: App) -> None:
    created = (await app.client.post("/api/reports", data={"text": "первый"}, headers=app.headers(ALICE))).json()
    for _ in range(2):
        r = await app.client.patch(
            f"/api/reports/{created['report_id']}",
            data={"text": "поправлено", "edit_key": "k-1"},
            headers=app.headers(ALICE),
        )
        assert r.status_code == 200 and r.json()["message"]


async def test_mila_can_give_herself_a_freeze(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    r = await app.client.post(f"/api/admin/participants/{ADMIN_ID}/freezes", json={"reason": "manual"}, headers=admin)
    assert r.status_code == 201 and r.json()["granted"] is True
    assert await app.jobs("telegram_notify") == [], "she does not get a message from herself"


async def test_the_people_list_says_reports_exist_when_the_stamp_was_removed(app: App) -> None:
    admin = app.headers(ADMIN_ID, "Мила")
    await app.client.post("/api/reports", data={"text": "есть отчёт"}, headers=app.headers(ALICE))
    await app.client.put(f"/api/admin/participants/{ALICE}/stamps/1", json={"level": None}, headers=admin)
    people_out = (await app.client.get("/api/admin/participants", headers=admin)).json()
    alice = next(p for p in people_out if p["id"] == ALICE)
    assert alice["week_level"] is None and alice["week_reports"] == 1


async def test_the_app_texts_speak_of_the_app(app: App) -> None:
    home = (await app.client.get("/api/home", headers=app.headers(ALICE))).json()
    assert "во вкладке «Сегодня»" in home["texts"]["greeting"]
    assert "пришли сюда" not in home["texts"]["greeting"]
    assert "Записалось не то, что нужно" in home["texts"]["help"]
    assert "в «Журнале»" in home["texts"]["help"]


async def test_a_meaning_without_a_word_is_refused(app: App) -> None:
    r = await app.client.post("/api/words", json={"text": " — значение"}, headers=app.headers(ALICE))
    assert r.status_code == 422


async def test_cancelling_an_unknown_report_is_a_404(app: App) -> None:
    r = await app.client.post("/api/reports/999999/cancel", headers=app.headers(ALICE))
    assert r.status_code == 404


async def test_too_many_parts_answer_in_russian(app: App) -> None:
    files = [("files", (f"p{i}.jpg", JPEG, "image/jpeg")) for i in range(45)]
    r = await app.client.post("/api/reports", files=files, headers=app.headers(ALICE))
    assert r.status_code == 413 and "файлов" in r.json()["detail"]
