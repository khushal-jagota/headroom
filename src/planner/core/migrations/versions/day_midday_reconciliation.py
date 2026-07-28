"""A day keeps the mid-day reconciliation separately from its morning overview.

Revision ID: day_midday_reconciliation
Revises: no_conversation_before_a_message
"""

from __future__ import annotations

from alembic import op

revision = "day_midday_reconciliation"
down_revision = "no_conversation_before_a_message"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE days ADD COLUMN midday_reconciliation TEXT NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE days DROP COLUMN midday_reconciliation")
