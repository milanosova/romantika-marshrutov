"""Transaction-scoped advisory locks for the «once per person» writes.

A duplicate check that reads and then writes is only as good as the isolation around it:
two requests sent at the same moment (a double tap, two devices, a retry) both read «no row
yet» and both write. Taking a lock keyed by the thing being written serialises them inside
Postgres, so the second one waits and then sees the first one's row.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def serialise(session: AsyncSession, key: str) -> None:
    """Hold the lock named by `key` until the transaction ends (`pg_advisory_xact_lock`)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})
