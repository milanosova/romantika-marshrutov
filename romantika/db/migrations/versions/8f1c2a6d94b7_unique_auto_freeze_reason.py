"""unique auto freeze reason

The «first word» and «first maximum» freezes are granted once per season and participant
(DOMAIN §3). Two concurrent updates — an album arrives as one update per photo — used to
pass the read-then-write check at the same time and grant the freeze twice, which inflates
the freeze total for the rest of the season. The partial unique index makes that impossible
regardless of how many workers run.

Revision ID: 8f1c2a6d94b7
Revises: 793d0a764d28
Create Date: 2026-09-03 21:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f1c2a6d94b7"
down_revision: str | None = "793d0a764d28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_freezes_auto_reason"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "freezes",
        ["season_id", "user_id", "reason"],
        unique=True,
        postgresql_where=sa.text("reason IN ('word', 'max')"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="freezes")
