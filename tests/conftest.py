"""Postgres fixtures for the whole test suite.

`TEST_DATABASE_URL` is used when set; otherwise a Postgres 16 container is started once per
session (Docker required). The schema is created by `alembic upgrade head`, never by
`metadata.create_all`, so the migrations themselves are under test.

Each test gets an `AsyncSession` bound to a connection with an open transaction; the session
works inside a SAVEPOINT, so an `IntegrityError` raised by a test only poisons that savepoint
and the outer transaction is still rolled back cleanly at teardown.
"""

from __future__ import annotations

import asyncio
import os

# macOS + Homebrew: WeasyPrint finds pango/cairo only with this hint (a no-op elsewhere).
if os.uname().sysname == "Darwin":
    os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")
from collections.abc import AsyncIterator, Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from romantika.config import PROJECT_ROOT
from romantika.db.session import make_engine

ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(PROJECT_ROOT / "romantika" / "db" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def run_alembic(url: str, revision: str, *, downgrade: bool = False) -> None:
    """Run alembic outside the event loop (its async env.py calls `asyncio.run`)."""
    config = alembic_config(url)
    if downgrade:
        command.downgrade(config, revision)
    else:
        command.upgrade(config, revision)


def existing_tables(url: str) -> list[str]:
    """Table names in the target database, read outside the event loop."""

    async def read() -> list[str]:
        engine = make_engine(url)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(lambda sync_conn: sa.inspect(sync_conn).get_table_names())
        finally:
            await engine.dispose()

    return asyncio.run(read())


def check_scratch_database(url: str) -> None:
    """Refuse to run against a database that is not ours to drop.

    The migration test runs `alembic downgrade base`, which deletes every table and all its
    rows. That is only acceptable on a scratch database: either one named `..._test` (as
    documented in `.env.example`) or one whose schema is still empty.
    """
    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if name.endswith("_test"):
        return
    tables = [table for table in existing_tables(url) if table != "alembic_version"]
    if tables:
        pytest.exit(
            "TEST_DATABASE_URL points at a non-empty database whose name does not end in '_test' "
            f"({name}: {len(tables)} tables). This suite drops the whole schema; "
            "point it at a scratch database instead.",
            returncode=2,
        )


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A migrated, empty Postgres 16 database for the whole test session."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        check_scratch_database(url)
        run_alembic(url, "head")
        yield url
        return

    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError as exc:  # pragma: no cover - dev environment issue
        pytest.skip(f"neither TEST_DATABASE_URL nor testcontainers available: {exc}")

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        container_url = container.get_connection_url()
        run_alembic(container_url, "head")
        yield container_url


@pytest.fixture(scope="session")
def alembic_url(database_url: str) -> str:
    """Alias used by migration tests that upgrade and downgrade the schema themselves."""
    return database_url


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()
