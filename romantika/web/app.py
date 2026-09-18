"""FastAPI application factory (ARCHITECTURE §8)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from romantika.config import Settings
from romantika.domain.calendar import moscow_now
from romantika.services.errors import Refused
from romantika.services.media import MediaStore
from romantika.texts import ru
from romantika.web.deps import AppState
from romantika.web.routes import admin_api, api, media, public

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _refused(_: Request, exc: Exception) -> JSONResponse:
    """A service refused the input (`services.errors.Refused`): the client's fault, said in Russian."""
    return JSONResponse({"detail": str(exc)}, status_code=422)


async def _invalid(_: Request, exc: Exception) -> JSONResponse:
    """A body failed validation: one Russian sentence instead of pydantic's JSON, since the
    Mini App shows `detail` to the person as is."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    first = errors[0] if errors else {}
    kind = str(first.get("type", ""))
    if kind == "string_too_long":
        detail = ru.API_TOO_LONG.format(limit=first.get("ctx", {}).get("max_length", ""))
    elif kind == "value_error":  # our own validators (schemas.py) already speak Russian
        detail = str(first.get("msg", "")).removeprefix("Value error, ") or ru.API_BAD_INPUT
    else:
        detail = ru.API_BAD_INPUT
    return JSONResponse({"detail": detail}, status_code=422)


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
    app.add_exception_handler(RequestValidationError, _invalid)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(public.router)
    app.include_router(api.router)
    app.include_router(admin_api.router)
    app.include_router(media.router)
    return app
