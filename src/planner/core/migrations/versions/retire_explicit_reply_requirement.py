"""Retire the explicit-reply requirement: its credits table and its projected view rows.

Stored explicit_reply_missing events stay in conversation_events and now project to
nothing. Their derived rows go, and a rest line that pointed at one falls back to the
latest remaining row with rest text. Each marker was written just before its turn's
turn_ended, which closes the work group anyway, so no turn or group layout changes.
"""

import sqlite3
from typing import cast

from alembic import op

from planner.conversation.views.writer import repair_rest_line_after_deleting_rows

revision = "retire_explicit_reply_requirement"
down_revision = "goal_holders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute("DROP TABLE conversation_reply_credits")
    pointing_at_deleted_rows = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT s.conversation_id FROM conversation_view_state s "
            "JOIN conversation_view_rows r ON r.conversation_id=s.conversation_id "
            "AND r.row_kind='explicit_reply_missing' AND r.sequence IN "
            "(s.latest_full_happened_sequence,s.latest_focus_happened_sequence)"
        )
    ]
    conn.execute("DELETE FROM conversation_view_rows WHERE row_kind='explicit_reply_missing'")
    for conversation_id in pointing_at_deleted_rows:
        repair_rest_line_after_deleting_rows(conn, conversation_id)


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
