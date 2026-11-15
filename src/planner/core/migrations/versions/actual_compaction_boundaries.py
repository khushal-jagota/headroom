"""Repair boundaries inflated by a later turn ending.

The compaction event records the actual boundary. The immediately preceding prompt
identifies Panels' own idle maintenance; other compactions belong to working turns.
Keep the scheduler's separate attempted-activity position unchanged.
"""

import sqlite3
from typing import cast

from alembic import op

revision = "actual_compaction_boundaries"
down_revision = "merge_iphone_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute(
        "UPDATE conversations AS c SET automatically_compacted_through_sequence="
        "COALESCE((SELECT MAX(e.sequence) FROM conversation_events e "
        "WHERE e.conversation_id=c.conversation_id AND e.kind='context_compacted'), 0) "
        "WHERE c.automatically_compacted_through_sequence > 0"
    )
    conn.execute(
        "UPDATE conversations AS c SET working_turn_compacted_through_sequence="
        "COALESCE((SELECT MAX(e.sequence) FROM conversation_events e "
        "WHERE e.conversation_id=c.conversation_id AND e.kind='context_compacted' "
        "AND NOT EXISTS (SELECT 1 FROM conversation_events p "
        "WHERE p.conversation_id=e.conversation_id AND p.kind='prompt' "
        "AND p.sequence=(SELECT MAX(q.sequence) FROM conversation_events q "
        "WHERE q.conversation_id=e.conversation_id AND q.kind='prompt' "
        "AND q.sequence < e.sequence) "
        "AND json_extract(p.payload,'$.sender_message_id') IS NULL "
        "AND json_extract(p.payload,'$.sender') IS NULL "
        "AND json_extract(p.payload,'$.sender_label')='Panels' "
        "AND json_extract(p.payload,'$.text')='/compact')), 0) "
        "WHERE c.working_turn_compacted_through_sequence > 0"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
