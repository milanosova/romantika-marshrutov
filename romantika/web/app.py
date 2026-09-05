"""FastAPI application factory (ARCHITECTURE §8)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.config import Settings
from romantika.domain.calendar import moscow_now
from romantika.services.errors import Refused
from romantika.services.media import MediaStore
from romantika.web.deps import AppState
from romantika.web.routes import admin_api, api, media, public

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _refused(_: Request, exc: Exception) -> JSONResponse:
    """A service refused the input (`services.errors.Refused`): the client's fault, said in Russian."""
    return JSONResponse({"detail": str(exc)}, status_code=422)


def create_app(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    media_store: MediaStore,
    *,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    app = FastAPI(title="Romantika Marshrutov", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.romantika = AppState(
        settings=settings,
        session_factory=session_factory,
        media_store=media_store,
        clock=clock or moscow_now,
    )
    app.add_exception_handler(Refused, _refused)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(public.router)
    app.include_router(api.router)
    app.include_router(admin_api.router)
    app.include_router(media.router)
    return app
