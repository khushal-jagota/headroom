"""Conversations and the rows they record.

The conversation system keeps two things: a conversation, which is the settings an agent
runs under and where its record has got to, and that record itself — one numbered run of
rows per conversation, each row a finished thing, written once and never touched again.

The table is `conversation_events` rather than `events`: the bare name belonged to the
entity event log dropped one revision earlier, and these rows are a different thing
entirely. Nothing is carried over from the conversation layer this one replaces; old
conversations stay where they are.

Revision ID: conversation_system_tables
Revises: ticket_status_changed_at
"""

from __future__ import annotations

from alembic import op

revision = "conversation_system_tables"
down_revision = "ticket_status_changed_at"
branch_labels = None
depends_on = None

CREATE_CONVERSATIONS = """
CREATE TABLE conversations (
  conversation_id   TEXT PRIMARY KEY,
  backend_key       TEXT NOT NULL,
  model             TEXT,
  reasoning_effort  TEXT,
  workspace_folder  TEXT NOT NULL,
  role_text         TEXT,
  identity_environment_variables TEXT NOT NULL DEFAULT '[]',
  access            TEXT NOT NULL,
  vendor_session_cursor TEXT,
  latest_sequence   INTEGER NOT NULL DEFAULT 0,
  created_at        INTEGER NOT NULL
)
"""

# The primary key is the ordering: rows are read as "everything after sequence N" for one
# conversation, which is the leading edge of this key and needs no index of its own.
CREATE_CONVERSATION_EVENTS = """
CREATE TABLE conversation_events (
  conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
  sequence        INTEGER NOT NULL,
  kind            TEXT NOT NULL,
  payload         TEXT NOT NULL,
  created_at      INTEGER NOT NULL,
  PRIMARY KEY (conversation_id, sequence)
)
"""


def upgrade() -> None:
    op.execute(CREATE_CONVERSATIONS)
    op.execute(CREATE_CONVERSATION_EVENTS)


def downgrade() -> None:
    raise NotImplementedError(
        "dropping these tables throws away every conversation's record, which nothing "
        "else holds"
    )
