"""Prepare demand-scoped workspace reads without granting runtime authority."""

import sqlite3
from typing import cast

from alembic import op

from planner.navigation.views.schema import prepare as prepare_navigation
from planner.resource_versions.data import install_source_triggers
from planner.resource_versions.registry import BOUNDED_WORKSPACE_SOURCES
from planner.tickets.workspace.schema import prepare as prepare_ticket_fields

revision = "bounded_workspace_reads"
down_revision = "goal_and_recap_for_orchestrators"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute("""CREATE TABLE resource_source_versions (
        source_kind TEXT NOT NULL, source_id TEXT NOT NULL, revision INTEGER NOT NULL,
        PRIMARY KEY(source_kind,source_id)
    )""")
    install_source_triggers(conn, BOUNDED_WORKSPACE_SOURCES)
    prepare_ticket_fields(conn)
    prepare_navigation(conn)
    conn.execute("""CREATE INDEX conversation_owner_message_latest
        ON conversation_events(conversation_id,sequence) WHERE kind='message_to_owner'""")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
