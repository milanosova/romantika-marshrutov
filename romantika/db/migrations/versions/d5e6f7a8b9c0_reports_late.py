"""reports.late: a report sent for a week that has already ended (DOMAIN §2, 17–18.09.2026)

A participant may add a report to a past week until the season ends — into the journal
only: it never earns a stamp, never gives a freeze back and never enters that week's
summary. The flag is what keeps it out of every stamp computation; the row is otherwise an
ordinary report.

Additive: every existing report is on time (`false`). The downgrade drops the only record
of which reports are late, and the previous release would count them for stamps on the next
recomputation, so it refuses while late reports exist (RUNBOOK «Rollback»).

Revision ID: d5e6f7a8b9c0
Revises: c4e8f1a2b9d3
Create Date: 2026-09-18 15:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4e8f1a2b9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("late", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    late = op.get_bind().execute(sa.text("SELECT count(*) FROM reports WHERE late")).scalar_one()
    if late:
        raise RuntimeError(
            f"{late} late report(s) exist; the previous release would count them for stamps. "
            "Keep this revision, or fix forward."
        )
    op.drop_column("reports", "late")
