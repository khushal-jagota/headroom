"""Keep the activity through which automatic compaction was attempted.

Revision ID: automatic_compaction_attempts
Revises: addressed_messages
"""

from __future__ import annotations

from alembic import op

revision = "automatic_compaction_attempts"
down_revision = "addressed_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversations ADD COLUMN "
        "automatic_compaction_attempted_through_sequence INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "UPDATE conversations SET automatic_compaction_attempted_through_sequence = "
        "automatically_compacted_through_sequence"
    )
    op.execute("DROP INDEX idx_conversations_automatic_compaction_due")
    op.execute(
        "CREATE INDEX idx_conversations_automatic_compaction_due "
        "ON conversations(latest_agent_activity_at, latest_agent_activity_sequence, "
        "automatic_compaction_attempted_through_sequence)"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "dropping the automatic compaction attempt marker can repeat maintenance work"
    )
