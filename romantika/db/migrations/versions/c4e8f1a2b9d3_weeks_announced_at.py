"""weeks.announced_at: a draft week versus an announced one

A week Mila creates ahead is a draft until she announces it (DOMAIN §1, 15.09.2026):
participants do not see a draft, it is never the current week and it costs nobody a
freeze. Announcing is a one-way step that requires a title and a minimum task, so a week
that carries stamps can never fall back to a draft and orphan them.

Every week that exists today was announced by construction (it came from the season
import), so the column is backfilled from `created_at`; new rows default to NULL.

The downgrade drops the only record of which weeks are drafts, and the previous release
shows every week to participants. With drafts present it therefore refuses: delete or
announce them first (RUNBOOK «Rollback»). Without drafts the round trip keeps every week
announced; the exact announcement moment is not kept (it comes back as `created_at`).

Revision ID: c4e8f1a2b9d3
Revises: b7d4e2a90c15
Create Date: 2026-09-15 01:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8f1a2b9d3"
down_revision: str | None = "b7d4e2a90c15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("weeks", sa.Column("announced_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE weeks SET announced_at = created_at WHERE announced_at IS NULL")


def downgrade() -> None:
    drafts = op.get_bind().execute(sa.text("SELECT count(*) FROM weeks WHERE announced_at IS NULL")).scalar_one()
    if drafts:
        raise RuntimeError(
            f"{drafts} draft week(s) exist; the previous release would show them to participants. "
            "Announce or delete them in the admin app, then downgrade."
        )
    op.drop_column("weeks", "announced_at")
