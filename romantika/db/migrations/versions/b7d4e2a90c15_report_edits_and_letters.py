"""report edits, idempotent submissions, letters to Mila

- `reports.client_id` + partial unique index: a Mini App retry after a lost response finds
  the report it already made (ARCHITECTURE §8.1).
- `reports.edited_at`: the participant may change text and files of a report while its
  week is open (DOMAIN §2); the previous text goes to `audit_log`.
- `letters`: everything sent to Mila that is not a report — «Написать Миле» in the bot or
  the app, a message outside a week, a report taken back — with her answer, so the admin
  app can show an inbox. `admin_links.letter_id` routes a chat reply to its letter.

Revision ID: b7d4e2a90c15
Revises: a3c9e1f27b04
Create Date: 2026-09-05 13:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d4e2a90c15"
down_revision: str | None = "a3c9e1f27b04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("client_id", sa.String(length=64), nullable=True))
    op.add_column("reports", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_reports_user_id_client_id",
        "reports",
        ["user_id", "client_id"],
        unique=True,
        postgresql_where=sa.text("client_id IS NOT NULL"),
    )
    op.create_table(
        "letters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source IN ('bot', 'app', 'out_of_week', 'not_report')", name="ck_letters_source"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], name="fk_letters_report_id_reports"),
        sa.ForeignKeyConstraint(["replied_by"], ["users.id"], name="fk_letters_replied_by_users"),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], name="fk_letters_season_id_seasons"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_letters_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_letters"),
    )
    op.create_index("ix_letters_season_id", "letters", ["season_id"])
    op.create_index("ix_letters_user_id", "letters", ["user_id"])
    op.create_index("ix_letters_season_id_created_at", "letters", ["season_id", "created_at"])
    op.add_column("admin_links", sa.Column("letter_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_admin_links_letter_id_letters", "admin_links", "letters", ["letter_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_admin_links_letter_id_letters", "admin_links", type_="foreignkey")
    op.drop_column("admin_links", "letter_id")
    op.drop_index("ix_letters_season_id_created_at", table_name="letters")
    op.drop_index("ix_letters_user_id", table_name="letters")
    op.drop_index("ix_letters_season_id", table_name="letters")
    op.drop_table("letters")
    op.drop_index("uq_reports_user_id_client_id", table_name="reports")
    op.drop_column("reports", "edited_at")
    op.drop_column("reports", "client_id")
