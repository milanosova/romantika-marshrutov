"""The participant Mini App API (ARCHITECTURE §8.1): the bot's flows over HTTP.

Reports with uploads, intents, level fixes, cancels, letters, words, facts, the dictionary,
the admin extras — and what each of them queues for the worker to send through Telegram.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.config import Settings
from romantika.db import models
from romantika.services import content, people, seed
from romantika.services.media import MediaStore
from romantika.services.people import TelegramUser
from romantika.web.app import create_app
from romantika.web.auth import build_init_data
from romantika.web.routes import api as api_routes

SEASON_JSON = Path(__file__).resolve().parents[2] / "data" / "seasons" / "mexico-2026.json"
ADMIN_ID = 355363829
ALICE = 1001
BOB = 1002
TOKEN = "123456:TEST-TOKEN"
JPEG = b"\xff\xd8\xff\xe0" + b"fake-jpeg-body" * 100


def moscow(y: int, m: int, d: int, hour: int = 12) -> datetime:
    return datetime(y, m, d, hour, 0, tzinfo=UTC) - timedelta(hours=3)


@dataclass
class App:
    client: AsyncClient
    session: AsyncSession
    store: MediaStore
    now: datetime
    season_id: int

    def headers(self, user_id: int, first_name: str = "Алиса") -> dict[str, str]:
        init = build_init_data(
            TOKEN,
            {"id": user_id, "first_name": first_name, "username": f"u{user_id}"},
            auth_date=int(self.now.timestamp()),
        )
        return {"X-Telegram-Init-Data": init}

    async def jobs(self, kind: str) -> list[models.Job]:
        rows = await self.session.execute(select(models.Job).where(models.Job.kind == kind).order_by(models.Job.id))
        return list(rows.scalars())

    async def stamp(self, user_id: int, week_number: int) -> str | None:
        week = await content.week_by_number(self.session, self.season_id, week_number)
        assert week is not None
        row = (
            await self.session.execute(
                select(models.Stamp).where(models.Stamp.user_id == user_id, models.Stamp.week_id == week.id)
            )
        ).scalar_one_or_none()
        return row.level if row else None


@pytest.fixture
async def app(db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> App:
    result = await seed.import_season(db_session, SEASON_JSON)
    await content.activate_season(db_session, result.season_id, actor_id=ADMIN_ID)
    now = moscow(2026, 9, 2, 15)  # week 1 is running
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
    monkeypatch.setenv("BOT_USERNAME", "romantika_test_bot")
    store = MediaStore(tmp_path / "media")
    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    application = create_app(Settings(), factory, store, clock=lambda: now)
    client = AsyncClient(transport=ASGITransport(app=application), base_url="https://romantika.example.test")
    return App(client=client, session=db_session, store=store, now=now, season_id=result.season_id)


# --- shells ------------------------------------------------------------------------


async def test_app_shell_serves_vendored_bridge_and_tab(app: App) -> None:
    for path, tab in (("/app", "today"), ("/app/journal", "journal"), ("/app/nonsense", "today")):
        r = await app.client.get(path)
        assert r.status_code == 200, path
        assert "/static/vendor/telegram-web-app.js" in r.text, "the bridge is served from here, not telegram.org"
        assert "telegram.org/js" not in r.text
        assert f'data-tab="{tab}"' in r.text
    r = await app.client.get("/app/admin")
    assert r.status_code == 200 and "admin.js" in r.text
    assert (await app.client.get("/static/vendor/telegram-web-app.js")).status_code == 200


# --- home and intent ---------------------------------------------------------------


async def test_home_has_task_today_passport_and_texts(app: App) -> None:
    assert (await app.client.get("/api/home")).status_code == 401
    r = await app.client.get("/api/home", headers=app.headers(ALICE))
    assert r.status_code == 200
    body = r.json()
    assert body["week"]["number"] == 1 and body["week"]["task_min"] and body["week"]["intent"] is None
    assert body["week"]["reports_count"] == 0 and body["week"]["level"] is None
    assert body["today"]["tzolkin"]["sign_name"] and body["today"]["word"]["word"]
    assert body["today"]["calendar_url"] == "https://romantika.example.test/calendar"
    assert body["passport"]["weeks_total"] == len(body["weeks"]) and body["passport"]["freezes_total"] == 2
    assert body["weeks"][1]["state"] == "locked" and body["weeks"][1]["task_min"] == "", "future weeks stay hidden"
    assert "Если что-то пошло не так" in body["texts"]["help"] and "<script" not in body["texts"]["help"]
    assert body["links"]["admin_app"] is False and body["links"]["bot_username"] == "romantika_test_bot"
    admin = (await app.client.get("/api/home", headers=app.headers(ADMIN_ID, "Мила"))).json()
    assert admin["links"]["admin_app"] is True


async def test_intent_is_stored_and_mila_is_told(app: App) -> None:
    r = await app.client.post("/api/intent", json={"week_number": 1, "choice": "take"}, headers=app.headers(ALICE))
    assert r.status_code == 200 and "берёшься" in r.json()["hint"]
    home = (await app.client.get("/api/home", headers=app.headers(ALICE))).json()
    assert home["week"]["intent"] == "take"
    (job,) = await app.jobs("telegram_notify")
    assert job.payload["chat_id"] == ADMIN_ID and "берусь" in job.payload["text"]
    assert job.payload["link"]["user_id"] == ALICE
    assert (
        await app.client.post("/api/intent", json={"week_number": 99, "choice": "try"}, headers=app.headers(ALICE))
    ).status_code == 404
    assert (
        await app.client.post("/api/intent", json={"week_number": 1, "choice": "maybe"}, headers=app.headers(ALICE))
    ).status_code == 422


async def test_mila_own_actions_are_not_copied_to_herself(app: App) -> None:
    r = await app.client.post(
        "/api/intent", json={"week_number": 1, "choice": "take"}, headers=app.headers(ADMIN_ID, "Мила")
    )
    assert r.status_code == 200
    assert await app.jobs("telegram_notify") == []


# --- reports -----------------------------------------------------------------------


async def test_text_report_stamps_minimum_and_queues_both_messages(app: App) -> None:
    r = await app.client.post("/api/reports", data={"text": "чимичанга"}, headers=app.headers(ALICE))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["level"] == "min" and body["stamp_level"] == "min" and body["out_of_week"] is False
    assert body["week_number"] == 1 and "минимум" in body["message"]
    assert await app.stamp(ALICE, 1) == "min"
    jobs = await app.jobs("telegram_notify")
    assert [j.payload["chat_id"] for j in jobs] == [ADMIN_ID, ALICE]
    admin_job, receipt = jobs
    assert "Отчёт за неделю 1" in admin_job.payload["text"] and "чимичанга" in admin_job.payload["text"]
    assert admin_job.payload["link"] == {
        "user_id": ALICE,
        "report_id": body["report_id"],
        "week_id": (await content.week_by_number(app.session, app.season_id, 1)).id,
        "letter_id": None,
    }  # type: ignore[union-attr]
    assert admin_job.payload["media_ids"] == []
    assert receipt.payload["text"] == body["message"] and "link" not in receipt.payload
    home = (await app.client.get("/api/home", headers=app.headers(ALICE))).json()
    assert home["week"]["reports_count"] == 1 and home["week"]["level"] == "min"


async def test_photo_upload_lands_on_disk_hashed_and_earns_the_star(app: App) -> None:
    r = await app.client.post(
        "/api/reports",
        data={"text": "тако удались"},
        files=[("files", ("photo.jpg", JPEG, "image/jpeg")), ("files", ("clip.mp4", b"mp4-bytes", "video/mp4"))],
        headers=app.headers(ALICE),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["level"] == "max" and body["stamp_level"] == "max" and body["freeze_granted"] is True
    assert "звёздочкой" in body["message"] and "заморозка" in body["message"]

    rows = list((await app.session.execute(select(models.Media).order_by(models.Media.created_at))).scalars())
    assert len(rows) == 2
    photo, clip = rows
    assert photo.mime == "image/jpeg" and photo.tg_file_id is None and photo.downloaded_at is not None
    assert photo.sha256 == hashlib.sha256(JPEG).hexdigest() and photo.size == len(JPEG)
    assert photo.path.startswith(f"mexico-2026/{ALICE}/") and photo.path.endswith(".jpg")
    assert app.store.full_path(photo.path).read_bytes() == JPEG
    assert clip.path.endswith(".mp4") and clip.sha256 == hashlib.sha256(b"mp4-bytes").hexdigest()
    assert not list(app.store.root.rglob("*.part")), "no half files left behind"

    admin_job = (await app.jobs("telegram_notify"))[0]
    assert admin_job.payload["chat_id"] == ADMIN_ID
    assert admin_job.payload["media_ids"] == [str(photo.id), str(clip.id)]
    assert "(photo)" not in admin_job.payload["text"] and "тако удались" in admin_job.payload["text"]

    # the owner and Mila see the file, others do not
    assert (await app.client.get(f"/media/{photo.id}", headers=app.headers(ALICE))).content == JPEG
    assert (await app.client.get(f"/media/{photo.id}", headers=app.headers(ADMIN_ID))).status_code == 200
    assert (await app.client.get(f"/media/{photo.id}", headers=app.headers(BOB))).status_code == 403
    journal = (await app.client.get("/api/journal", headers=app.headers(ALICE))).json()
    assert journal["reports"][0]["media"][0]["downloaded"] is True


async def test_upload_limits_and_empty_reports(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (await app.client.post("/api/reports", data={"text": "   "}, headers=app.headers(ALICE))).status_code == 422
    too_many = [("files", (f"{i}.jpg", JPEG, "image/jpeg")) for i in range(api_routes.MAX_UPLOAD_FILES + 1)]
    assert (await app.client.post("/api/reports", files=too_many, headers=app.headers(ALICE))).status_code == 413
    monkeypatch.setattr(api_routes, "MAX_UPLOAD_BYTES", 16)
    r = await app.client.post(
        "/api/reports", files=[("files", ("big.jpg", JPEG, "image/jpeg"))], headers=app.headers(ALICE)
    )
    assert r.status_code == 413 and "МБ" in r.json()["detail"]
    assert (await app.session.execute(select(models.Media))).scalars().first() is None, "rolled back"
    assert (await app.session.execute(select(models.Report))).scalars().first() is None
    assert not list(app.store.root.rglob("*")) or not [p for p in app.store.root.rglob("*") if p.is_file()]
    assert await app.stamp(ALICE, 1) is None


async def test_cancel_recomputes_stamp_and_level_fix_never_downgrades(app: App) -> None:
    first = (await app.client.post("/api/reports", data={"text": "раз"}, headers=app.headers(ALICE))).json()
    assert await app.stamp(ALICE, 1) == "min"
    fix = (await app.client.post("/api/weeks/1/level", json={"level": "max"}, headers=app.headers(ALICE))).json()
    assert fix["ok"] is True and fix["stamp_level"] == "max"
    down = (await app.client.post("/api/weeks/1/level", json={"level": "min"}, headers=app.headers(ALICE))).json()
    assert down["ok"] is False and "не понижаю" in down["message"] and await app.stamp(ALICE, 1) == "max"

    r = await app.client.post(f"/api/reports/{first['report_id']}/cancel", headers=app.headers(ALICE))
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["stamp_level"] is None
    assert await app.stamp(ALICE, 1) is None
    again = (await app.client.post(f"/api/reports/{first['report_id']}/cancel", headers=app.headers(ALICE))).json()
    assert again["ok"] is False and "уже отменён" in again["message"]
    assert (
        await app.client.post(f"/api/reports/{first['report_id']}/cancel", headers=app.headers(BOB))
    ).status_code == 403
    letter = [j for j in await app.jobs("telegram_notify") if "сначала пришло как отчёт" in (j.payload["text"] or "")]
    assert len(letter) == 1 and letter[0].payload["link"]["report_id"] == first["report_id"]
    none = (await app.client.post("/api/weeks/1/level", json={"level": "max"}, headers=app.headers(BOB))).json()
    assert none["ok"] is False and "отчёта нет" in none["message"]


# --- words, facts, letters ---------------------------------------------------------


async def test_word_fact_letter_and_dictionary(app: App) -> None:
    r = await app.client.post(
        "/api/words", json={"text": "sobremesa — время за столом после еды"}, headers=app.headers(ALICE)
    )
    assert r.status_code == 201 and r.json()["word"] == "sobremesa" and r.json()["freeze_granted"] is True
    home = (await app.client.get("/api/home", headers=app.headers(ALICE))).json()
    assert home["passport"]["freezes_total"] == 3 and home["passport"]["freeze_reasons"] == ["word"]
    d = (await app.client.get("/api/dictionary", headers=app.headers(BOB, "Боб"))).json()
    assert d["week_words"][0]["week_number"] == 1 and len(d["week_words"]) == 1, "only released weeks"
    assert d["user_words"][0]["word"] == "sobremesa" and d["user_words"][0]["mine"] is False
    assert d["user_words"][0]["author"] == "Алиса"

    assert (
        await app.client.post("/api/facts", json={"text": "Ацтеки называли себя мешика"}, headers=app.headers(ALICE))
    ).status_code == 201
    assert (
        await app.client.post("/api/facts", json={"text": "От Милы"}, headers=app.headers(ADMIN_ID, "Мила"))
    ).status_code == 201
    facts = (await app.client.get("/api/facts", headers=app.headers(ALICE))).json()
    assert [f["mine"] for f in facts["facts"]] == [True, False]
    assert facts["facts"][0]["author"] == "Алиса" and facts["facts"][1]["author"] is None

    r = await app.client.post(
        "/api/letters", json={"text": "Мила, я оставила комментарий!"}, headers=app.headers(ALICE)
    )
    assert r.status_code == 200 and "Передала" in r.json()["message"]
    texts = [j.payload["text"] for j in await app.jobs("telegram_notify")]
    assert any("Новое слово от" in t for t in texts) and any("Новый факт от" in t for t in texts)
    assert any("Сообщение от" in t and "комментарий" in t for t in texts)
    assert (await app.client.post("/api/letters", json={"text": ""}, headers=app.headers(ALICE))).status_code == 422


# --- admin extras ------------------------------------------------------------------


async def test_admin_reminders_remind_and_message(app: App) -> None:
    assert (await app.client.get("/api/admin/reminders", headers=app.headers(ALICE))).status_code == 403
    admin = app.headers(ADMIN_ID, "Мила")
    assert (await app.client.get("/api/admin/reminders", headers=admin)).json() == {"enabled": True}
    assert (await app.client.put("/api/admin/reminders", json={"enabled": False}, headers=admin)).json() == {
        "enabled": False
    }
    assert (await app.client.get("/api/admin/reminders", headers=admin)).json() == {"enabled": False}
    r = await app.client.post("/api/admin/remind", headers=admin)
    assert r.status_code == 202
    (job,) = await app.jobs("reminders_now")
    assert job.payload == {"season_id": app.season_id, "requested_by": ADMIN_ID, "week_number": None}
    r = await app.client.post(f"/api/admin/participants/{ALICE}/message", json={"text": "Молодец!"}, headers=admin)
    assert r.status_code == 202
    (note,) = await app.jobs("telegram_notify")
    assert (
        note.payload["chat_id"] == ALICE
        and "Сообщение от Милы" in note.payload["text"]
        and "Молодец" in note.payload["text"]
    )
    assert (
        await app.client.post("/api/admin/participants/424242/message", json={"text": "x"}, headers=admin)
    ).status_code == 404
