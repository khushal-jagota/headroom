"""Drop the WebSocket session's executor tables once nothing reads them.

executor_mailboxes copied the active connection and the contact status into the
presence row and the incarnation rows. The WebSocket transport that wrote
executor_connections and mac_contact_status is gone, so both tables are dropped.
"""

import sqlite3
from typing import cast

from alembic import op

revision = "retire_executor_websocket_tables"
down_revision = "stall_alarm_preference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    # Dropping the table drops its executor_one_active_connection index with it.
    conn.execute("DROP TABLE executor_connections")
    conn.execute("DROP TABLE mac_contact_status")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
