"""Install additive diagnostic omission metadata for honest conversation reads."""

import sqlite3
from typing import cast

from alembic import op

from planner.conversation.views.schema import EARLIER_MESSAGES_INDEX_SQL

revision = "diagnostic_expiry_metadata"
down_revision = "open_work_group_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute("""
        CREATE TABLE conversation_expired_ranges (
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
            first_sequence INTEGER NOT NULL CHECK(first_sequence > 0),
            last_sequence INTEGER NOT NULL CHECK(last_sequence >= first_sequence),
            cutoff_unix INTEGER NOT NULL,
            expired_at_unix INTEGER NOT NULL,
            event_count INTEGER NOT NULL CHECK(event_count = last_sequence-first_sequence+1),
            PRIMARY KEY(conversation_id,first_sequence)
        )
    """)
    conn.execute(EARLIER_MESSAGES_INDEX_SQL)


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
