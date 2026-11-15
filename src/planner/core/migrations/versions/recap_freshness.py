"""Record recap writes independently of unrelated record activity.

Older nonempty recaps retain unknown age. Ticket stage changes after a known recap
remain visible until the next recap write, including changes within one second.
"""

import sqlite3
from typing import cast

from alembic import op

revision = "recap_freshness"
down_revision = "ticket_decision_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute("ALTER TABLE tickets ADD COLUMN recap_updated_at INTEGER")
    conn.execute(
        "ALTER TABLE tickets ADD COLUMN recap_stage_changed INTEGER NOT NULL DEFAULT 0 "
        "CHECK(recap_stage_changed IN (0,1))"
    )
    conn.execute("ALTER TABLE managers ADD COLUMN recap_updated_at INTEGER")
    # An installed navigation snapshot already uses Signal publications. Retain that
    # source while the recap projection changes. Fresh databases remain prepared until
    # their normal Signal installation supplies the first complete snapshot.
    if conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] == 10:
        from planner.navigation.data import rebuild

        conn.execute("UPDATE navigation_meta SET schema_version=11")
        previous_factory = conn.row_factory
        try:
            conn.row_factory = sqlite3.Row
            rebuild(conn)
        finally:
            conn.row_factory = previous_factory


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
