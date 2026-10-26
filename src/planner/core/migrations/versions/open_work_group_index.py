"""Index the open work groups a Full-lens page actually asks for."""

import sqlite3
from typing import cast

from alembic import op

OPEN_GROUP_INDEX = (
    "CREATE INDEX IF NOT EXISTS conversation_view_groups_open ON "
    "conversation_view_groups(conversation_id, latest_entry_sequence, group_key, turn_key) "
    "WHERE ending_sequence IS NULL"
)

revision = "open_work_group_index"
down_revision = "worker_server_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute(OPEN_GROUP_INDEX)


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
