"""media.tg_file_id nullable

Files can now arrive through the Mini App as a direct upload (ARCHITECTURE §8.1): such a
file never had a Telegram `file_id`, so the column becomes optional. The worker fills it in
when the file is first sent to Telegram (the copy to Mila's chat), so the bot can keep
re-sending journal photos by id afterwards. Nothing is deleted; downgrade only restores the
constraint and refuses when an uploaded file exists.

Revision ID: a3c9e1f27b04
Revises: 8f1c2a6d94b7
Create Date: 2026-09-04 12:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c9e1f27b04"
down_revision: str | None = "8f1c2a6d94b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("media", "tg_file_id", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.alter_column("media", "tg_file_id", existing_type=sa.Text(), nullable=False)
