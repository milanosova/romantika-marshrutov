"""Late reports: a past week takes text and photos into the journal only (DOMAIN §2)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.config import Settings
from romantika.db import models
from romantika.pdf.journal import render_journal_html
from romantika.services import content, journal, people, seed, summary
from romantika.services.media import MediaStore
from romantika.services.people import TelegramUser
from romantika.web.app import create_app
from tests.integration.test_web_miniapp import ADMIN_ID, ALICE, BOB, JPEG, SEASON_JSON, TOKEN, App, moscow

WEEK3 = moscow(2026, 9, 16, 15)  # weeks 1 and 2 are behind, the season runs until 18.11


async def make_app(db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, now: datetime) -> App:
    result = await seed.import_season(db_session, SEASON_JSON)
    await content.activate_season(db_session, result.season_id, actor_id=ADMIN_ID)
    for uid, name in ((ADMIN_ID, "Мила"), (ALICE, "Алиса"), (BOB, "Боб")):
        await people.upsert_user(db_session, TelegramUser(id=uid, first_name=name), now=moscow(2026, 8, 20))
        await people.ensure_member(db_session, result.season_id, uid, now=moscow(2026, 8, 20))
    await db_session.flush()
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("ADMIN_IDS", str(ADMIN_ID))
    monkeypatch.setenv("ADMIN_CHAT_ID", str(ADMIN_ID))
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unused/unused")
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://romantika.example.test")
    store = MediaStore(tmp_path / "media")
    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    application = create_app(Settings(), factory, store, clock=lambda: now)
    client = AsyncClient(transport=ASGITransport(app=application), base_url="https://romantika.example.test")
    return App(client=client, session=db_session, store=store, now=now, season_id=result.season_id)


@pytest.fixture
async def app(db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> App:
    return await make_app(db_session, tmp_path, monkeypatch, WEEK3)


async def freezes_of(app: App, user_id: int) -> int:
    rows = await app.session.execute(select(models.Freeze).where(models.Freeze.user_id == user_id))
    return len(list(rows.scalars()))


async def test_a_late_report_goes_into_the_journal_without_a_stamp(app: App) -> None:
    home = (await app.client.get("/api/home", headers=app.headers(ALICE))).json()
    by_number = {w["number"]: w for w in home["weeks"]}
    assert by_number[1]["late_open"] and by_number[2]["late_open"], "past weeks take late reports"
    assert not by_number[3]["late_open"] and not by_number[4]["late_open"], "not the running or a future week"

    r = await app.client.post(
        "/api/reports",
        data={"text": "нашла antojo — тако аль пастор", "week_number": "1", "client_id": "late-1"},
        files=[("files", ("a.jpg", JPEG, "image/jpeg"))],
        headers=app.headers(ALICE),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["late"] is True and body["week_number"] == 1 and body["stamp_level"] is None
    assert body["freeze_granted"] is False and "Записала в журнал недели 1" in body["message"]
    assert await app.stamp(ALICE, 1) is None, "a photo for a past week earns no stamp"
    assert await freezes_of(app, ALICE) == 0, "and no first-maximum freeze"

    # Mila's copy is headed as a late one and carries no stamp; the participant gets the receipt.
    (job,) = [j for j in await app.jobs("telegram_notify") if j.payload["chat_id"] == ADMIN_ID]
    assert "дослала за неделю 1" in job.payload["text"] and "Штамп не ставится" in job.payload["text"]
    assert job.payload["link"]["report_id"] == body["report_id"]
    (receipt,) = [j for j in await app.jobs("telegram_notify") if j.payload["chat_id"] == ALICE]
    assert "Записала в журнал недели 1" in receipt.payload["text"]

    # The journal shows it in the chapter of week 1, marked and editable until the season ends.
    j = (await app.client.get("/api/journal", headers=app.headers(ALICE))).json()
    (report,) = [x for x in j["reports"] if x["week_number"] == 1]
    assert report["late"] is True and report["editable"] is True
    assert j["passport"]["stamps"] == 0

    # A retry with the same client_id answers with the same report, still late.
    again = await app.client.post(
        "/api/reports",
        data={"text": "нашла antojo", "week_number": "1", "client_id": "late-1"},
        headers=app.headers(ALICE),
    )
    assert again.status_code == 200 and again.json()["report_id"] == body["report_id"] and again.json()["late"] is True


async def test_second_late_report_adds_to_the_chapter_and_the_summary_ignores_both(app: App) -> None:
    first = await app.client.post(
        "/api/reports", data={"text": "первая запись", "week_number": "2"}, headers=app.headers(ALICE)
    )
    second = await app.client.post(
        "/api/reports", data={"text": "вторая запись", "week_number": "2"}, headers=app.headers(ALICE)
    )
    assert "Записала в журнал" in first.json()["message"]
    assert "Дописала в журнал" in second.json()["message"], "the second one joins the chapter"

    week2 = await content.week_by_number(app.session, app.season_id, 2)
    assert week2 is not None
    view = await summary.week(app.session, season_id=app.season_id, week_number=2, today=app.now.date())
    assert view.reports_total == 0 and ALICE not in view.submitted, "the week's summary is what happened then"

    j = (await app.client.get("/api/journal", headers=app.headers(ALICE))).json()
    assert [x["late"] for x in j["reports"] if x["week_number"] == 2] == [True, True]


async def test_late_reports_are_refused_for_the_running_and_future_weeks(app: App) -> None:
    running = await app.client.post("/api/reports", data={"text": "x", "week_number": "3"}, headers=app.headers(ALICE))
    assert running.status_code == 422 and "ещё идёт" in running.json()["detail"]
    future = await app.client.post("/api/reports", data={"text": "x", "week_number": "5"}, headers=app.headers(ALICE))
    assert future.status_code == 422 and "ещё не началась" in future.json()["detail"]
    nowhere = await app.client.post("/api/reports", data={"text": "x", "week_number": "99"}, headers=app.headers(ALICE))
    assert nowhere.status_code == 422 and "такой недели нет" in nowhere.json()["detail"]
    junk = await app.client.post("/api/reports", data={"text": "x", "week_number": "abc"}, headers=app.headers(ALICE))
    assert junk.status_code == 422
    to_mila = [j for j in await app.jobs("telegram_notify") if j.payload["chat_id"] == ADMIN_ID]
    assert to_mila == [], "nothing reached Mila"


async def test_after_the_season_nothing_can_be_added(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = await make_app(db_session, tmp_path, monkeypatch, moscow(2026, 11, 20, 12))
    home = (await app.client.get("/api/home", headers=app.headers(ALICE))).json()
    assert not any(w["late_open"] for w in home["weeks"]), "the season is over"
    r = await app.client.post("/api/reports", data={"text": "x", "week_number": "1"}, headers=app.headers(ALICE))
    assert r.status_code == 422 and "сезон закончился" in r.json()["detail"]


async def test_a_late_report_never_holds_the_stamp_of_an_on_time_one(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling the on-time report removes the stamp even though a late one stays (DOMAIN §2)."""
    early = await make_app(db_session, tmp_path, monkeypatch, moscow(2026, 9, 2, 15))  # week 1 runs
    on_time = (await early.client.post("/api/reports", data={"text": "вовремя"}, headers=early.headers(ALICE))).json()
    assert await early.stamp(ALICE, 1) == "min"

    late = await make_app(db_session, tmp_path, monkeypatch, WEEK3)
    r = await late.client.post("/api/reports", data={"text": "потом", "week_number": "1"}, headers=late.headers(ALICE))
    assert r.status_code == 201 and "Дописала" in r.json()["message"] and r.json()["stamp_level"] == "min"
    assert await late.stamp(ALICE, 1) == "min", "the stamp the week already had stays"

    cancel = await late.client.post(f"/api/reports/{on_time['report_id']}/cancel", headers=late.headers(ALICE))
    assert cancel.status_code == 200 and cancel.json()["stamp_level"] is None
    assert await late.stamp(ALICE, 1) is None, "the late report does not keep the stamp alive"


async def test_a_late_report_can_be_edited_and_taken_back_until_the_season_ends(app: App) -> None:
    r = (
        await app.client.post("/api/reports", data={"text": "черновик", "week_number": "1"}, headers=app.headers(ALICE))
    ).json()
    edit = await app.client.patch(
        f"/api/reports/{r['report_id']}", data={"text": "чистовик", "edit_key": "e1"}, headers=app.headers(ALICE)
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["report"]["text"] == "чистовик" and edit.json()["report"]["late"] is True
    assert "Запись в журнале" in edit.json()["message"] and edit.json()["stamp_level"] is None
    assert await app.stamp(ALICE, 1) is None

    cancel = await app.client.post(f"/api/reports/{r['report_id']}/cancel", headers=app.headers(ALICE))
    assert cancel.status_code == 200 and "убрала из журнала" in cancel.json()["message"]
    j = (await app.client.get("/api/journal", headers=app.headers(ALICE))).json()
    assert [x for x in j["reports"] if x["week_number"] == 1] == []


async def test_journal_and_pdf_carry_the_late_mark(app: App) -> None:
    await app.client.post(
        "/api/reports", data={"text": "дослала за первую"}, headers=app.headers(ALICE)
    )  # an on-time report of the running week 3
    await app.client.post(
        "/api/reports", data={"text": "за столом: тако аль пастор", "week_number": "1"}, headers=app.headers(ALICE)
    )
    view = await journal.build(app.session, season_id=app.season_id, user_id=ALICE, today=app.now.date())
    chapters = {week.number: week for week in view.weeks}
    assert chapters[1].late_only and chapters[1].level is None and chapters[1].entries[0].late is True
    assert not chapters[3].late_only and chapters[3].entries[0].late is False
    html = render_journal_html(view)
    assert "дослано позже" in html and "Неделя 1" in html
    assert '<div class="n">1 <small>/ 12</small></div><div class="l">неделя со штампом</div>' in html, (
        "the cover counts stamps, not chapters"
    )
