"""Stage 4 acceptance: FastAPI web — Mini App auth, API, admin API, media, public page.

READ-ONLY for implementers. Contract used here:
- `romantika.web.app.create_app(settings, session_factory, media_store, *, clock=None) -> FastAPI`
- `romantika.web.auth.build_init_data(bot_token, user: dict, *, auth_date: int) -> str` — the
  inverse of validation (used by tests and the dev bypass); `validate_init_data(init_data,
  bot_token, *, now) -> InitDataUser | None`.
- Header `X-Telegram-Init-Data` carries the raw initData string.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.config import Settings
from romantika.db import models
from romantika.domain.types import ReportKind, StampLevel
from romantika.services import content, people, reports, seed
from romantika.services.gateways import TelegramFile
from romantika.services.media import MediaStore
from romantika.services.people import TelegramUser
from romantika.services.reports import IncomingFile, IncomingMessage
from romantika.web.app import create_app
from romantika.web.auth import build_init_data, validate_init_data

SEASON_JSON = Path(__file__).resolve().parents[2] / "data" / "seasons" / "mexico-2026.json"
ADMIN_ID = 355363829
ALICE = 1001
BOB = 1002
TOKEN = "123456:TEST-TOKEN"


def moscow(y: int, m: int, d: int, hour: int = 12) -> datetime:
    return datetime(y, m, d, hour, 0, tzinfo=UTC) - timedelta(hours=3)


@dataclass
class FakeTelegram:
    payload: bytes = b"fake-jpeg-bytes"
    calls: list[str] = field(default_factory=list)

    async def get_file(self, file_id: str) -> TelegramFile:
        return TelegramFile(file_path=f"photos/{file_id}.jpg", file_size=len(self.payload))

    async def download_file(self, file_path: str, destination: Path) -> None:
        self.calls.append(file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payload)


@dataclass
class Web:
    client: AsyncClient
    now: datetime
    media_id: str
    season_id: int
    week1_id: int

    def headers(self, user_id: int, first_name: str = "Алиса") -> dict[str, str]:
        init = build_init_data(TOKEN, {"id": user_id, "first_name": first_name, "username": "u" + str(user_id)}, auth_date=int(self.now.timestamp()))
        return {"X-Telegram-Init-Data": init}


@pytest.fixture
async def web(db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Web:
    result = await seed.import_season(db_session, SEASON_JSON)
    await content.activate_season(db_session, result.season_id, actor_id=ADMIN_ID)
    now = moscow(2026, 9, 2, 15)
    for uid, name in ((ADMIN_ID, "Мила"), (ALICE, "Алиса"), (BOB, "Боб")):
        await people.upsert_user(db_session, TelegramUser(id=uid, username=None, first_name=name, last_name=None), now=moscow(2026, 8, 20))
        await people.ensure_member(db_session, result.season_id, uid, now=moscow(2026, 8, 20))
    message = IncomingMessage(
        kind=ReportKind.PHOTO,
        text="тако удались",
        tg_chat_id=ALICE,
        tg_message_id=1,
        files=[IncomingFile(kind=ReportKind.PHOTO, file_id="AAA", file_unique_id="u-AAA", mime="image/jpeg", size=None, width=1280, height=960)],
    )
    accepted = await reports.accept(db_session, season_id=result.season_id, user_id=ALICE, message=message, now=now)
    store = MediaStore(tmp_path / "media")
    await store.download(db_session, accepted.media_ids[0], FakeTelegram(), now=now)
    week1 = await content.current_week(db_session, result.season_id, today=now.date())
    assert week1 is not None
    await db_session.flush()

    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("ADMIN_IDS", str(ADMIN_ID))
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unused/unused")
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://romantika.example.test")
    settings = Settings()
    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    app = create_app(settings, factory, store, clock=lambda: now)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="https://romantika.example.test")
    return Web(client=client, now=now, media_id=str(accepted.media_ids[0]), season_id=result.season_id, week1_id=week1.id)


# --- auth ---------------------------------------------------------------------------


def test_init_data_roundtrip_and_tamper() -> None:
    now = moscow(2026, 9, 2)
    init = build_init_data(TOKEN, {"id": ALICE, "first_name": "Алиса"}, auth_date=int(now.timestamp()))
    user = validate_init_data(init, TOKEN, now=now)
    assert user is not None and user.id == ALICE and user.first_name == "Алиса"
    assert validate_init_data(init.replace("1001", "1002"), TOKEN, now=now) is None, "tampered user id"
    assert validate_init_data(init, "999:OTHER", now=now) is None
    assert validate_init_data(init, TOKEN, now=now + timedelta(hours=25)) is None, "auth_date older than 24h"


async def test_me_requires_valid_init_data(web: Web) -> None:
    assert (await web.client.get("/api/me")).status_code == 401
    assert (await web.client.get("/api/me", headers={"X-Telegram-Init-Data": "hash=deadbeef"})).status_code == 401
    r = await web.client.get("/api/me", headers=web.headers(ALICE))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == ALICE and body["is_admin"] is False
    r = await web.client.get("/api/me", headers=web.headers(ADMIN_ID, "Мила"))
    assert r.json()["is_admin"] is True


# --- public pages --------------------------------------------------------------------


async def test_healthz(web: Web) -> None:
    r = await web.client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["db"] is True


async def test_public_page_hides_future_weeks(web: Web) -> None:
    r = await web.client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    html = r.text
    assert "Мексика" in html and "За столом" in html
    assert "Красками" not in html, "week 2 has not started on 2026-09-02"
    assert "Привал" not in html
    assert "Алиса" not in html and str(ALICE) not in html, "no participant data on the public page"


async def test_calendar_page_and_journal_shell(web: Web) -> None:
    r = await web.client.get("/calendar")
    assert r.status_code == 200 and "Имиш" in r.text and "Ахау" in r.text and "telegram-web-app.js" in r.text
    r = await web.client.get("/app/journal")
    assert r.status_code == 200 and "telegram-web-app.js" in r.text
    r = await web.client.get("/app/admin")
    assert r.status_code == 200


# --- journal API and media -------------------------------------------------------------


async def test_journal_api_for_owner(web: Web) -> None:
    r = await web.client.get("/api/journal", headers=web.headers(ALICE))
    assert r.status_code == 200
    body = r.json()
    assert body["season"]["title"] == "Мексика"
    assert body["passport"]["stamps"] == 1 and body["passport"]["freezes_total"] == 3
    weeks = {w["number"]: w for w in body["weeks"]}
    assert weeks[1]["state"] == "stamped" and weeks[1]["level"] == "max" and weeks[1]["title"] == "За столом"
    assert all(w["number"] <= 1 or w["state"] in ("locked", "current") for w in body["weeks"])
    assert body["reports"][0]["text"] == "тако удались"
    media = body["reports"][0]["media"]
    assert media[0]["url"].endswith(f"/media/{web.media_id}")


async def test_media_is_private_to_owner_and_admin(web: Web) -> None:
    url = f"/media/{web.media_id}"
    assert (await web.client.get(url)).status_code == 401
    assert (await web.client.get(url, headers=web.headers(BOB, "Боб"))).status_code == 403
    r = await web.client.get(url, headers=web.headers(ALICE))
    assert r.status_code == 200 and r.content == b"fake-jpeg-bytes" and r.headers["content-type"] == "image/jpeg"
    assert "private" in r.headers.get("cache-control", "")
    assert (await web.client.get(url, headers=web.headers(ADMIN_ID, "Мила"))).status_code == 200
    assert (await web.client.get("/media/00000000-0000-0000-0000-000000000000", headers=web.headers(ALICE))).status_code == 404


async def test_pdf_request_enqueues_job(web: Web, db_session: AsyncSession) -> None:
    r = await web.client.post("/api/journal/pdf", headers=web.headers(ALICE))
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    job = await db_session.get(models.Job, job_id)
    assert job is not None and job.kind == "journal_pdf" and job.payload["user_id"] == ALICE
    r = await web.client.get(f"/api/journal/pdf/{job_id}", headers=web.headers(ALICE))
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert (await web.client.get(f"/api/journal/pdf/{job_id}", headers=web.headers(BOB, "Боб"))).status_code == 403


# --- admin API -----------------------------------------------------------------------------


async def test_admin_api_is_admin_only(web: Web) -> None:
    assert (await web.client.get("/api/admin/weeks")).status_code == 401
    assert (await web.client.get("/api/admin/weeks", headers=web.headers(ALICE))).status_code == 403
    r = await web.client.get("/api/admin/weeks", headers=web.headers(ADMIN_ID, "Мила"))
    assert r.status_code == 200 and len(r.json()) == 12


async def test_admin_edits_week_with_audit(web: Web, db_session: AsyncSession) -> None:
    r = await web.client.put(f"/api/admin/weeks/{web.week1_id}", headers=web.headers(ADMIN_ID, "Мила"), json={"task_min": "Новый минимум", "word": "sobremesa"})
    assert r.status_code == 200 and r.json()["task_min"] == "Новый минимум"
    audit = (await db_session.execute(select(func.count()).select_from(models.AuditLog).where(models.AuditLog.entity == "week"))).scalar_one()
    assert audit == 1
    bad = await web.client.put(f"/api/admin/weeks/{web.week1_id}", headers=web.headers(ADMIN_ID, "Мила"), json={"number": 5})
    assert bad.status_code in (400, 422), "week number is not editable"


async def test_admin_participants_stamps_freezes_achievements(web: Web, db_session: AsyncSession) -> None:
    h = web.headers(ADMIN_ID, "Мила")
    r = await web.client.get("/api/admin/participants", headers=h)
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert {ALICE, BOB} <= ids

    r = await web.client.put(f"/api/admin/participants/{BOB}/stamps/1", headers=h, json={"level": "max"})
    assert r.status_code == 200 and r.json()["level"] == "max"
    r = await web.client.post(f"/api/admin/participants/{BOB}/freezes", headers=h, json={"reason": "comment", "note": "за комментарий"})
    assert r.status_code == 201
    r = await web.client.post(f"/api/admin/participants/{BOB}/achievements", headers=h, json={"code_or_text": "повар"})
    assert r.status_code == 201 and r.json()["label"] == "🌮 Повар"
    r = await web.client.put(f"/api/admin/participants/{BOB}/wish", headers=h, json={"text": "Молодец"})
    assert r.status_code == 200

    r = await web.client.get(f"/api/admin/participants/{BOB}", headers=h)
    body = r.json()
    assert body["passport"]["stamps"] == 1 and body["achievements"] == ["🌮 Повар"] and body["wish"] == "Молодец"
    stamp = (await db_session.execute(select(models.Stamp).where(models.Stamp.user_id == BOB))).scalar_one()
    assert stamp.level == StampLevel.MAX.value and stamp.source == "admin"


async def test_admin_summary_and_facts(web: Web) -> None:
    h = web.headers(ADMIN_ID, "Мила")
    r = await web.client.get("/api/admin/summary", headers=h, params={"week": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["members_total"] == 3 and body["reports_total"] == 1 and ALICE in [s["user_id"] for s in body["submitted"]]

    r = await web.client.post("/api/admin/facts", headers=h, json={"text": "Ацтеки называли себя мешика"})
    assert r.status_code == 201
    fact_id = r.json()["id"]
    r = await web.client.get("/api/admin/facts", headers=h)
    assert [f["text"] for f in r.json()] == ["Ацтеки называли себя мешика"]
    assert (await web.client.delete(f"/api/admin/facts/{fact_id}", headers=h)).status_code == 204
    assert (await web.client.get("/api/admin/facts", headers=h)).json() == []


async def test_static_assets_and_no_secrets_in_html(web: Web) -> None:
    for path in ("/", "/calendar", "/app/journal", "/app/admin"):
        html = (await web.client.get(path)).text
        assert TOKEN not in html and hashlib.sha256(TOKEN.encode()).hexdigest() not in html
