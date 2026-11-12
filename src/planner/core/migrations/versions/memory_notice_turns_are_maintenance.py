"""Stop stored memory-notice turns from authorizing one more automatic compaction.

A Worker memory-loss notice is maintenance, and its turn no longer renews compaction
eligibility. Before that change its turn ending advanced the activity watermark, so a
conversation whose only prompts since its last automatic attempt are notices is still due
one more ``/compact``. Its attempt watermark moves up to its activity, as if the notice had
always been maintenance. The automatic ``/compact`` row itself is not work either. Any
other prompt keeps the conversation eligible. ``automatically_compacted_through_sequence``
stays: it is the real compaction boundary that notices answer.
"""

import sqlite3
from typing import cast

from alembic import op

revision = "memory_notice_turns_are_maintenance"
down_revision = "retire_executor_websocket_tables"
branch_labels = None
depends_on = None

# Frozen copies of what the notice and the automatic /compact were written with.
MEMORY_NOTICE_SENDER_MESSAGE_ID_PREFIX = "worker_step_memory_notice_"
AUTOMATIC_COMPACTION_TEXT = "/compact"
AUTOMATIC_COMPACTION_SENDER_LABEL = "Panels"

PROMPTS_AFTER_THE_LAST_ATTEMPT = (
    "SELECT 1 FROM conversation_events e WHERE e.conversation_id=c.conversation_id "
    "AND e.kind='prompt' AND e.sequence > c.automatic_compaction_attempted_through_sequence "
)
IS_MEMORY_NOTICE = (
    "substr(COALESCE(json_extract(e.payload,'$.sender_message_id'),''),1,:prefix_length)=:prefix"
)
IS_AUTOMATIC_COMPACTION = (
    "(json_extract(e.payload,'$.sender_message_id') IS NULL "
    "AND json_extract(e.payload,'$.sender') IS NULL "
    "AND json_extract(e.payload,'$.sender_label') IS :label "
    "AND json_extract(e.payload,'$.text') IS :text)"
)


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute(
        "UPDATE conversations AS c "
        "SET automatic_compaction_attempted_through_sequence=latest_agent_activity_sequence "
        "WHERE latest_agent_activity_sequence > automatic_compaction_attempted_through_sequence "
        f"AND EXISTS ({PROMPTS_AFTER_THE_LAST_ATTEMPT} AND {IS_MEMORY_NOTICE}) "
        f"AND NOT EXISTS ({PROMPTS_AFTER_THE_LAST_ATTEMPT} "
        f"AND NOT {IS_MEMORY_NOTICE} AND NOT {IS_AUTOMATIC_COMPACTION})",
        {
            "prefix": MEMORY_NOTICE_SENDER_MESSAGE_ID_PREFIX,
            "prefix_length": len(MEMORY_NOTICE_SENDER_MESSAGE_ID_PREFIX),
            "label": AUTOMATIC_COMPACTION_SENDER_LABEL,
            "text": AUTOMATIC_COMPACTION_TEXT,
        },
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
