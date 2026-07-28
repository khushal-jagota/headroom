"""Let go of conversations nothing was ever said in.

A conversation used to be made before there was anything to say: opening a Ticket's panel
and pressing Enter created one from stored defaults, and only then sent the message. The
message therefore arrived asking to run on something else, and for a backend that takes its
model when its process starts that means starting over — under a session id that had been
written down but never written into. Those conversations cannot take a message at all.

A conversation with no prompt row has never been spoken into, so under the rule this
release brings in it does not exist. Every Ticket and agent pointing at one is let go of,
which leaves them exactly where New leaves them: no conversation, and the next message
makes a real one.

The conversation rows stay. Nothing points at them and nothing will resume them, and
deleting a record is not something a repair should do.

Revision ID: no_conversation_before_a_message
Revises: conversation_available_commands
"""

from __future__ import annotations

from alembic import op

revision = "no_conversation_before_a_message"
down_revision = "conversation_available_commands"
branch_labels = None
depends_on = None

NOTHING_WAS_EVER_SAID_IN_IT = """
    conversation_id IS NOT NULL
    AND conversation_id NOT IN (
        SELECT conversation_id FROM conversation_events WHERE kind = 'prompt'
    )
"""


def upgrade() -> None:
    op.execute(f"UPDATE tickets SET conversation_id = NULL WHERE {NOTHING_WAS_EVER_SAID_IN_IT}")
    op.execute(f"UPDATE agents SET conversation_id = NULL WHERE {NOTHING_WAS_EVER_SAID_IN_IT}")


def downgrade() -> None:
    # There is nothing to put back. What this cleared were links to conversations that
    # could not take a message, so restoring them would restore the failure and nothing
    # else — and which owner pointed at which is not written down anywhere else.
    pass
