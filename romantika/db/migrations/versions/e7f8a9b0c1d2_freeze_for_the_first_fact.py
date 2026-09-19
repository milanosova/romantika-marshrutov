"""freeze for the first fact

The first word of a season earns a freeze; from 19.09.2026 the first own fact earns one
too (DOMAIN §3, Mila's decision). That needs a new `reason` value — the CHECK constraint
lists them — and the «once per season» partial unique index has to cover it, or two
concurrent adds would grant two freezes for the same reason.

Additive and reversible: downgrade refuses while `fact` freezes exist, because the older
code cannot read a value its CHECK constraint forbids.

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
Create Date: 2026-09-19 00:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK_NAME = "reason"  # the naming convention makes it `ck_freezes_reason`
INDEX_NAME = "uq_freezes_auto_reason"
OLD_REASONS = "'word', 'max', 'comment', 'meetup', 'friend', 'manual'"
NEW_REASONS = "'word', 'max', 'fact', 'comment', 'meetup', 'friend', 'manual'"


def upgrade() -> None:
    op.drop_constraint(CHECK_NAME, "freezes", type_="check")
    op.create_check_constraint(CHECK_NAME, "freezes", f"reason IN ({NEW_REASONS})")
    op.drop_index(INDEX_NAME, table_name="freezes")
    op.create_index(
        INDEX_NAME,
        "freezes",
        ["season_id", "user_id", "reason"],
        unique=True,
        postgresql_where=sa.text("reason IN ('word', 'max', 'fact')"),
    )


def downgrade() -> None:
    granted = op.get_bind().execute(sa.text("SELECT count(*) FROM freezes WHERE reason = 'fact'")).scalar_one()
    if granted:
        raise RuntimeError(
            f"{granted} freeze(s) were granted for a first fact: the previous release cannot read them. "
            "Fix forward, or ask Mila before touching participant data (CLAUDE.md rule 1)."
        )
    op.drop_index(INDEX_NAME, table_name="freezes")
    op.create_index(
        INDEX_NAME,
        "freezes",
        ["season_id", "user_id", "reason"],
        unique=True,
        postgresql_where=sa.text("reason IN ('word', 'max')"),
    )
    op.drop_constraint(CHECK_NAME, "freezes", type_="check")
    op.create_check_constraint(CHECK_NAME, "freezes", f"reason IN ({OLD_REASONS})")
