"""A conversation keeps the commands its agent says a person may type at it.

The list belongs to the conversation rather than to the child process that reported it,
because the moment a person most needs it is the moment before there is a child: opening a
conversation to write the first message is exactly when nothing is running. Held in memory
it would be gone every time the agent was, and the menu would be empty precisely when it
was wanted.

An empty list is the honest answer both for a conversation nothing has reported for yet
and for one whose backend said it has no commands, and nothing here tries to tell those
two apart.

Revision ID: conversation_available_commands
Revises: one_conversation_system
"""

from __future__ import annotations

from alembic import op

revision = "conversation_available_commands"
down_revision = "one_conversation_system"
branch_labels = None
depends_on = None

ADD_COLUMN = """
ALTER TABLE conversations ADD COLUMN available_commands TEXT NOT NULL DEFAULT '[]'
"""


def upgrade() -> None:
    op.execute(ADD_COLUMN)


def downgrade() -> None:
    # Nothing is lost by going back: a backend reports its whole list again on the next
    # session it establishes, so the column is a copy of something the agent still knows.
    op.execute("ALTER TABLE conversations DROP COLUMN available_commands")
