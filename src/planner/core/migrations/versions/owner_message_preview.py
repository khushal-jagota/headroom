"""Rebuild bounded rest summaries with the independent owner reply preview."""

import sqlite3
from typing import cast

from alembic import op

from planner.conversation.views.contracts import VIEW_PROJECTION_VERSION
from planner.conversation.views.writer import rebuild_projection

revision = "owner_message_preview"
down_revision = "review_work_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    if conn.execute(
        "SELECT 1 FROM conversation_view_state WHERE projection_version != ? LIMIT 1",
        (VIEW_PROJECTION_VERSION,),
    ).fetchone():
        rebuild_projection(conn)


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
