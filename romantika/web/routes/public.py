"""Server-rendered pages: the public season page, the calendar, the Mini App shells."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from romantika.domain.tzolkin import SIGNS, TONES, tzolkin_day
from romantika.services import achievements, content
from romantika.web.deps import SessionDep, SettingsDep, TodayDep

router = APIRouter(tags=["public"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _asset_version() -> str:
    """Short hash of the static files: a changed script gets a new URL, so no stale caches."""
    import hashlib

    digest = hashlib.sha256()
    static = Path(__file__).resolve().parent.parent / "static"
    for path in sorted(static.rglob("*")):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


templates.env.globals["asset_version"] = _asset_version()


def _signs_payload() -> dict[str, object]:
    return {
        "signs": [
            {
                "name": s.name,
                "name_academic": s.name_academic,
                "latin": s.latin,
                "emoji": s.emoji,
                "symbol": s.symbol,
                "meaning": s.meaning,
                "destiny": s.destiny,
                "short": s.short,
                "day_advice": s.day_advice,
            }
            for s in SIGNS
        ],
        "tones": [{"number": t.number, "name": t.name, "text": t.text} for t in TONES],
    }


@router.get("/healthz")
async def healthz(session: SessionDep) -> JSONResponse:
    ok = (await session.execute(text("SELECT 1"))).scalar_one() == 1
    return JSONResponse({"status": "ok" if ok else "degraded", "db": ok})


@router.get("/", response_class=HTMLResponse)
async def season_page(request: Request, session: SessionDep, settings: SettingsDep, today: TodayDep) -> HTMLResponse:
    season = await content.active_season(session, today=today)
    context: dict[str, object] = {"settings": settings, "today": today, "season": season}
    if season is not None:
        weeks = await content.weeks(session, season.id)
        released = [w for w in weeks if w.starts_on <= today]
        context.update(
            weeks_total=len(weeks),
            released=released,
            passed=sum(1 for w in weeks if w.ends_on < today),
            current=next((w for w in weeks if w.starts_on <= today <= w.ends_on), None),
            ahead=len(weeks) - len(released),
            catalogue=await achievements.catalogue(session, season.id),
            tzolkin=tzolkin_day(today) if season.daily_kind == "tzolkin" else None,
        )
    return templates.TemplateResponse(request, "season.html", context)


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, session: SessionDep, settings: SettingsDep, today: TodayDep) -> HTMLResponse:
    season = await content.active_season(session, today=today)
    context = {
        "settings": settings,
        "today": today,
        "season": season,
        "today_tzolkin": tzolkin_day(today),
        "payload": _signs_payload(),
    }
    return templates.TemplateResponse(request, "calendar.html", context)


APP_TABS = ("today", "passport", "journal", "words", "more")


@router.get("/app", response_class=HTMLResponse)
@router.get("/app/{tab}", response_class=HTMLResponse)
async def participant_app(request: Request, settings: SettingsDep, tab: str = "today") -> HTMLResponse:
    """The participant Mini App: one shell, the tab to open comes from the path (`/app/journal`)."""
    if tab == "admin":
        return await admin_app(request, settings)
    return templates.TemplateResponse(
        request, "app.html", {"settings": settings, "tab": tab if tab in APP_TABS else "today"}
    )


@router.get("/app/admin", response_class=HTMLResponse)
async def admin_app(request: Request, settings: SettingsDep) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin_app.html", {"settings": settings})
