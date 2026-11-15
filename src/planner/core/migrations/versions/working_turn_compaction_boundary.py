"""Record the compaction boundary that a working turn produced, apart from Panels' own.

Panels runs its own ``/compact`` after a conversation sits idle, and the Worker was not
part-way through anything then. Only a compaction inside a working turn owes a Worker the
mid-step memory-loss notice, so that boundary gets its own column.

Existing rows start from ``automatically_compacted_through_sequence``, so a working-turn
boundary that is still unanswered keeps its notice. A row whose boundary Panels' own
``/compact`` set starts at zero. That turn is recognised as a Panels ``/compact`` prompt
after the boundary (an idle ``/compact`` is maintenance, so its boundary is the activity
before it), followed by a recorded compaction with no other prompt between. A later
working-turn compaction moves the boundary past that prompt, so it is never mistaken for
an idle one. Any row the rule cannot recognise keeps the copied boundary: the cost is one
extra notice, never a Worker left without one.
"""

import sqlite3
from typing import cast

from alembic import op

revision = "working_turn_compaction_boundary"
down_revision = "recap_freshness"
branch_labels = None
depends_on = None

# Frozen copies of what the automatic /compact was written with.
AUTOMATIC_COMPACTION_TEXT = "/compact"
AUTOMATIC_COMPACTION_SENDER_LABEL = "Panels"

IS_AUTOMATIC_COMPACTION = (
    "(json_extract(p.payload,'$.sender_message_id') IS NULL "
    "AND json_extract(p.payload,'$.sender') IS NULL "
    "AND json_extract(p.payload,'$.sender_label') IS :label "
    "AND json_extract(p.payload,'$.text') IS :text)"
)
BOUNDARY_SET_BY_PANELS_OWN_COMPACT = (
    "EXISTS (SELECT 1 FROM conversation_events p "
    "JOIN conversation_events e ON e.conversation_id=p.conversation_id "
    "AND e.kind='context_compacted' AND e.sequence > p.sequence "
    "WHERE p.conversation_id=c.conversation_id AND p.kind='prompt' "
    "AND p.sequence > c.automatically_compacted_through_sequence "
    f"AND {IS_AUTOMATIC_COMPACTION} "
    "AND NOT EXISTS (SELECT 1 FROM conversation_events q "
    "WHERE q.conversation_id=p.conversation_id AND q.kind='prompt' "
    "AND q.sequence > p.sequence AND q.sequence < e.sequence))"
)


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute(
        "ALTER TABLE conversations ADD COLUMN "
        "working_turn_compacted_through_sequence INTEGER NOT NULL DEFAULT 0"
    )
    conn.execute(
        "UPDATE conversations AS c "
        "SET working_turn_compacted_through_sequence=automatically_compacted_through_sequence "
        "WHERE automatically_compacted_through_sequence > 0 "
        f"AND NOT {BOUNDARY_SET_BY_PANELS_OWN_COMPACT}",
        {"label": AUTOMATIC_COMPACTION_SENDER_LABEL, "text": AUTOMATIC_COMPACTION_TEXT},
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
