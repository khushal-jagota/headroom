"""The agents that are not a Ticket's, and where each has got to.

A Ticket's worker keeps its configuration on disk under worker settings and its live
state — which conversation it is currently having — on the Ticket row. The Chief keeps
its configuration the same way, under the same service, and had nowhere for the second
half. This is that row.

It is a table of agents rather than of conversation links, because a row here is a thing
rather than a relationship: the next fact that is true about an agent belongs on the
agent's row, and naming it for the link would have needed a second table to hold one.

It holds an agent and the conversation it is currently having, and nothing else. A column
nothing reads is not free, so a name, a status and a created-at are all absent until
something needs one.

Revision ID: agents
Revises: conversation_system_tables
"""

from __future__ import annotations

from alembic import op

revision = "agents"
down_revision = "conversation_system_tables"
branch_labels = None
depends_on = None

CREATE_AGENTS = """
CREATE TABLE agents (
  agent_key       TEXT PRIMARY KEY,
  conversation_id TEXT
)
"""


def upgrade() -> None:
    op.execute(CREATE_AGENTS)


def downgrade() -> None:
    op.execute("DROP TABLE agents")
