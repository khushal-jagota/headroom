"""Keep the durable policy state for automatic conversation compaction.

Revision ID: conversation_automatic_compaction
Revises: drop_supervisor_wake_and_ping
"""

from __future__ import annotations

from alembic import op

revision = "conversation_automatic_compaction"
down_revision = "drop_supervisor_wake_and_ping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversations ADD COLUMN latest_agent_activity_at INTEGER")
    op.execute(
        "ALTER TABLE conversations ADD COLUMN latest_agent_activity_sequence "
        "INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE conversations ADD COLUMN automatically_compacted_through_sequence "
        "INTEGER NOT NULL DEFAULT 0"
    )
    # Automatic compaction did not exist before this revision, so every historical agent
    # event and turn ending is ordinary activity. Prompts, answers, and compaction boundary
    # rows are deliberately absent: they came from the user or describe maintenance.
    agent_activity_kinds = (
        "'agent_message','token_usage','tool_call_started','tool_call_finished',"
        "'plan_updated','permission_asked','user_input_requested','user_input_failed',"
        "'turn_ended'"
    )
    op.execute(
        "UPDATE conversations SET latest_agent_activity_sequence = COALESCE(("
        "SELECT MAX(sequence) FROM conversation_events "
        "WHERE conversation_events.conversation_id = conversations.conversation_id "
        f"AND kind IN ({agent_activity_kinds})), 0), latest_agent_activity_at = ("
        "SELECT created_at FROM conversation_events "
        "WHERE conversation_events.conversation_id = conversations.conversation_id "
        f"AND kind IN ({agent_activity_kinds}) ORDER BY sequence DESC LIMIT 1)"
    )
    op.execute(
        "UPDATE conversations SET automatically_compacted_through_sequence = "
        "latest_agent_activity_sequence WHERE EXISTS (SELECT 1 FROM conversation_events boundary "
        "WHERE boundary.conversation_id = conversations.conversation_id "
        "AND boundary.kind = 'context_compacted') AND NOT EXISTS ("
        "SELECT 1 FROM conversation_events prompt WHERE prompt.conversation_id = "
        "conversations.conversation_id AND prompt.kind = 'prompt' AND prompt.sequence > ("
        "SELECT MAX(boundary.sequence) FROM conversation_events boundary WHERE "
        "boundary.conversation_id = conversations.conversation_id "
        "AND boundary.kind = 'context_compacted') AND EXISTS (SELECT 1 FROM "
        "conversation_events activity WHERE activity.conversation_id = "
        "conversations.conversation_id AND activity.sequence > prompt.sequence "
        f"AND activity.kind IN ({agent_activity_kinds})))"
    )
    op.execute(
        "CREATE INDEX idx_conversations_automatic_compaction_due "
        "ON conversations(latest_agent_activity_at, latest_agent_activity_sequence, "
        "automatically_compacted_through_sequence)"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "dropping the automatic compaction markers can repeat maintenance work"
    )
