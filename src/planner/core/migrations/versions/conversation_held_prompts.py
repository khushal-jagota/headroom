"""Store the conversation waiting line, so a restart keeps what was still waiting.

Revision ID: conversation_held_prompts
Revises: supervisor_obligations
"""

from __future__ import annotations

from alembic import op

revision = "conversation_held_prompts"
down_revision = "supervisor_obligations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Order is the row's own rowid. A waiting message never moves in the line, it only
    # leaves it, so there is no position to keep in step.
    op.execute(
        "CREATE TABLE conversation_held_prompts ("
        "held_prompt_id TEXT PRIMARY KEY,"
        "conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) "
        "ON DELETE CASCADE,"
        "content TEXT NOT NULL,"
        "sender_label TEXT NOT NULL,"
        "sender_message_id TEXT,"
        "sent_at_unix_milliseconds INTEGER,"
        "snapshot_sent_at_unix_milliseconds INTEGER NOT NULL,"
        "model_change TEXT,"
        "reasoning_effort_change TEXT,"
        "created_at INTEGER NOT NULL)"
    )
    op.execute(
        "CREATE INDEX idx_conversation_held_prompts_conversation "
        "ON conversation_held_prompts (conversation_id)"
    )
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"Conversation held prompt migration failed: {violations!r}")


def downgrade() -> None:
    raise NotImplementedError("dropping the held prompt table loses messages still waiting")
