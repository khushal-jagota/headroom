"""Add owner read positions and an addressed-message lookup index.

Revision ID: addressed_messages
Revises: feedback_notes
"""

from __future__ import annotations

from alembic import op

revision = "addressed_messages"
down_revision = "feedback_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversations ADD COLUMN owner_read_through_sequence "
        "INTEGER NOT NULL DEFAULT 0 CHECK (owner_read_through_sequence >= 0)"
    )
    op.execute(
        "CREATE INDEX idx_conversation_events_kind_recipient "
        "ON conversation_events(kind, "
        "json_extract(payload,'$.recipient.kind'), "
        "json_extract(payload,'$.recipient.id'))"
    )


def downgrade() -> None:
    raise NotImplementedError("addressed messages are not removed")
