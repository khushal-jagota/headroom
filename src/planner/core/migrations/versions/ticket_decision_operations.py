"""Keep Review decision receipts for the lifetime of their Ticket."""

import sqlite3
from typing import cast

from alembic import op

revision = "ticket_decision_operations"
down_revision = "retire_executor_websocket_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute(
        "CREATE TABLE ticket_decision_operations ("
        "ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE, "
        "operation_id TEXT NOT NULL, actor_kind TEXT NOT NULL, actor_id TEXT NOT NULL, "
        "command_json TEXT NOT NULL, receipt_json TEXT NOT NULL, created_at INTEGER NOT NULL, "
        "PRIMARY KEY(operation_id))"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
