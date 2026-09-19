"""freezes ceiling: five to six

A season has two base freezes and a ceiling for the earned ones. Since 19.09.2026 the bot
grants three of them by itself — the first own word, the first own fact, the first maximum
— which left no room under the old ceiling of five for the ones Mila gives by hand (a
comment, a meetup, a friend). Mila's decision: raise the ceiling to six.

Rewrites data (`seasons.max_freezes`), so it runs after a fresh backup — see RUNBOOK
«Release checklist». Only seasons that still carry the old default are touched; a ceiling
Mila set by hand is left alone. Reversible: downgrade puts six back to five, and the freezes
themselves are never deleted (CLAUDE.md rule 1) — a season that already handed out six keeps
them, and the next grant is refused until the count falls under the ceiling.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-09-19 02:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_CEILING = 5
NEW_CEILING = 6


def upgrade() -> None:
    op.alter_column("seasons", "max_freezes", server_default=sa.text(str(NEW_CEILING)))
    op.execute(sa.text(f"UPDATE seasons SET max_freezes = {NEW_CEILING} WHERE max_freezes = {OLD_CEILING}"))


def downgrade() -> None:
    op.alter_column("seasons", "max_freezes", server_default=sa.text(str(OLD_CEILING)))
    op.execute(sa.text(f"UPDATE seasons SET max_freezes = {OLD_CEILING} WHERE max_freezes = {NEW_CEILING}"))
