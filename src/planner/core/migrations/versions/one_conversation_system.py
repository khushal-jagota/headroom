"""What the conversation layer that came before left in the database.

Five tables belonged to it and nothing writes them any more: the step runs it recorded,
its own conversation rows, its cache of what each backend could be configured as, the
durable binding between an employee and a backend session, and the per-ticket projection
of what an agent was doing that the Workspace dot used to read. The code that filled them
is gone; these are the holes it left.

The Ticket's own link comes with them, by name. `employee_session_id` was the durable
session id of a backend process, and mirrored a binding table that owned it. It has held
a conversation id since the swap — the caller-owned name a conversation is known by
everywhere — and the binding table it mirrored is one of the five going here. A column
that says one thing and holds another is a trap for the next reader, and doing it later
would be a second migration for nothing.

Revision ID: one_conversation_system
Revises: agents
"""

from __future__ import annotations

from alembic import op

revision = "one_conversation_system"
down_revision = "agents"
branch_labels = None
depends_on = None

# The index on employee_step_runs goes with its table; SQLite drops it there.
RETIRED_TABLES: tuple[str, ...] = (
    "conversation_session_bindings",
    "employee_conversations",
    "employee_configuration_catalog_cache",
    "employee_step_runs",
    "ticket_conversation_projections",
)


def upgrade() -> None:
    for table in RETIRED_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("ALTER TABLE tickets RENAME COLUMN employee_session_id TO conversation_id")


def downgrade() -> None:
    raise NotImplementedError(
        "the rows in those five tables were written by a conversation layer that no "
        "longer exists, so there is nothing to put back into them"
    )
