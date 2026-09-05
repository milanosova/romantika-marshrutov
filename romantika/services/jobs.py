"""The job queue the worker runs on (ARCHITECTURE §6.1).

Claiming uses `FOR UPDATE SKIP LOCKED`, so several workers can share one table without ever
handing the same job out twice. A failed job comes back with an exponentially growing delay
and gives up after five attempts, which is where an alert to Mila belongs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models

#: First retry after a minute, then 2, 4, 8 — the fifth failure gives up.
BACKOFF = timedelta(minutes=1)
MAX_ATTEMPTS = 5

#: A claimed job whose worker died is nobody's after this: `claim` puts it back in the queue.
LEASE = timedelta(minutes=15)

#: How many abandoned jobs one `claim` picks up before serving the caller.
RECLAIM_BATCH = 20


@dataclass(frozen=True, slots=True)
class JobDTO:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    run_after: datetime


def backoff_for(attempts: int) -> timedelta:
    """Delay before retry number `attempts` (1 → 1 min, 2 → 2 min, 3 → 4 min, ...)."""
    return BACKOFF * (1 << max(attempts - 1, 0))


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    *,
    now: datetime,
    run_after: datetime | None = None,
) -> int:
    """Put one job on the queue; `run_after` delays it (reminders, retries)."""
    row = models.Job(
        kind=kind,
        payload=payload,
        status=models.JobStatus.QUEUED.value,
        run_after=run_after or now,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return row.id


async def reclaim_abandoned(session: AsyncSession, *, now: datetime) -> int:
    """Requeue jobs a dead worker left `running`; returns how many were taken back.

    Without this a worker that dies between `claim` and `finish` leaves the row `running`
    forever: `claim` only looks at `queued`, so the job would never be retried and never
    reach `failed`, and nobody would ever be told (ARCHITECTURE §6.1).
    """
    query = (
        select(models.Job)
        .where(
            models.Job.status == models.JobStatus.RUNNING.value,
            models.Job.started_at.is_not(None),
            models.Job.started_at <= now - LEASE,
        )
        .order_by(models.Job.started_at, models.Job.id)
        .limit(RECLAIM_BATCH)
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.execute(query)).scalars())
    for row in rows:
        _fail(row, error="lease expired: the worker never finished this job", now=now)
    if rows:
        await session.flush()
    return len(rows)


async def claim(session: AsyncSession, *, now: datetime) -> JobDTO | None:
    """Take the oldest job that is due, locking it against the other workers."""
    await reclaim_abandoned(session, now=now)
    query = (
        select(models.Job)
        .where(
            models.Job.status == models.JobStatus.QUEUED.value,
            models.Job.run_after <= now,
        )
        .order_by(models.Job.run_after, models.Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    row = (await session.execute(query)).scalar_one_or_none()
    if row is None:
        return None
    row.status = models.JobStatus.RUNNING.value
    row.started_at = now
    await session.flush()
    return JobDTO(id=row.id, kind=row.kind, payload=dict(row.payload), attempts=row.attempts, run_after=row.run_after)


async def finish(
    session: AsyncSession,
    job_id: int,
    *,
    error: str | None,
    now: datetime,
    result: dict[str, Any] | None = None,
) -> models.JobStatus:
    """Close a claimed job: done, or queued again with a backoff, or failed for good.

    `result` (e.g. the path of a rendered PDF) is merged into the payload so that the
    requester can find what the job produced.
    """
    row = await session.get(models.Job, job_id)
    if row is None:
        raise LookupError(f"job {job_id} does not exist")

    if error is None:
        row.status = models.JobStatus.DONE.value
        row.finished_at = now
        row.error = None
        if result:
            row.payload = {**row.payload, **result}
    else:
        _fail(row, error=error, now=now)
    await session.flush()
    return models.JobStatus(row.status)


def _fail(row: models.Job, *, error: str, now: datetime) -> None:
    """One failed attempt: back to the queue with a growing delay, or given up on."""
    row.attempts += 1
    row.error = error
    if row.attempts >= MAX_ATTEMPTS:
        row.status = models.JobStatus.FAILED.value
        row.finished_at = now
    else:
        row.status = models.JobStatus.QUEUED.value
        row.run_after = now + backoff_for(row.attempts)
        row.finished_at = None


@dataclass(frozen=True, slots=True)
class JobDetail:
    id: int
    kind: str
    status: str
    payload: dict[str, Any]
    attempts: int
    error: str | None
    finished_at: datetime | None


async def get(session: AsyncSession, job_id: int) -> JobDetail | None:
    row = await session.get(models.Job, job_id)
    if row is None:
        return None
    return JobDetail(
        id=row.id,
        kind=row.kind,
        status=row.status,
        payload=dict(row.payload),
        attempts=row.attempts,
        error=row.error,
        finished_at=row.finished_at,
    )
