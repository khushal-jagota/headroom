"""Retain the reason and compared facts for every obsolete proposal notice."""

import sqlite3
from typing import cast

from alembic import op

revision = "notice_suppression_audit"
down_revision = "rename_managers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    retained_schema = tuple(
        str(row[0]) for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name='conversation_lifecycle_facts' "
            "AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name"
        )
    )
    conn.execute(
        "CREATE TABLE conversation_lifecycle_facts_with_suppression ("
        "conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),"
        "kind TEXT NOT NULL CHECK(kind IN ('turn','attempt','request')),"
        "identity TEXT NOT NULL, turn_number INTEGER NOT NULL CHECK(turn_number > 0),"
        "operation TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN "
        "('intent','running','pending','answering','accepted','refused','unknown',"
        "'completed','failed','interrupted','unavailable','answered','started','injected',"
        "'suppressed')),provider_identity TEXT, PRIMARY KEY(conversation_id,kind,identity))"
    )
    conn.execute(
        "INSERT INTO conversation_lifecycle_facts_with_suppression "
        "SELECT * FROM conversation_lifecycle_facts"
    )
    conn.execute("DROP TABLE conversation_lifecycle_facts")
    conn.execute(
        "ALTER TABLE conversation_lifecycle_facts_with_suppression "
        "RENAME TO conversation_lifecycle_facts"
    )
    for statement in retained_schema:
        conn.execute(statement)
    conn.execute(
        "CREATE TABLE manager_wake_suppressions ("
        "wake_id INTEGER PRIMARY KEY, ticket_id TEXT, source_revision INTEGER NOT NULL, "
        "target_kind TEXT NOT NULL, target_id TEXT NOT NULL, reason TEXT NOT NULL, "
        "ticket_exists INTEGER NOT NULL, current_revision INTEGER, "
        "current_target_kind TEXT, current_target_id TEXT, "
        "sender_message_id TEXT, created_at INTEGER NOT NULL)"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
