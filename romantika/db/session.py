"""Engine and session factory. Services receive a session; they never open one."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(url, echo=echo, pool_pre_ping=True, future=True)


def make_session_factory(url: str, *, echo: bool = False) -> async_sessionmaker[AsyncSession]:
    """Session factory for an entrypoint (bot, web, worker, tests)."""
    return async_sessionmaker(bind=make_engine(url, echo=echo), expire_on_commit=False, autoflush=False)
