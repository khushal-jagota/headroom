"""What an agent that is not a Ticket is currently talking in.

A Ticket keeps its conversation on the row that owns the work. The Chief owns work too
and has no such row, so this is where an agent's current conversation is recorded. It is
keyed by agent rather than named for the Chief: the Chief is simply the first row, and
the next agent that owns a conversation costs a row rather than a table.

There is no timestamp and no nullable link, because nothing reads either. An agent with
no conversation has no row; starting one writes it and New deletes it.

Revision ID: agent_conversations
Revises: conversation_system_tables
"""

from __future__ import annotations

from alembic import op

revision = "agent_conversations"
down_revision = "conversation_system_tables"
branch_labels = None
depends_on = None

CREATE_AGENT_CONVERSATIONS = """
CREATE TABLE agent_conversations (
  agent_key       TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL
)
"""


def upgrade() -> None:
    op.execute(CREATE_AGENT_CONVERSATIONS)


def downgrade() -> None:
    op.execute("DROP TABLE agent_conversations")
