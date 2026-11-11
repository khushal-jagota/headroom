"""Retain Ticket end times independently from the Ticket itself."""

import sqlite3
from typing import cast

from alembic import op

revision = "ticket_workspace_lifecycle"
down_revision = "retire_executor_websocket_tables"
branch_labels = None
depends_on = None

# Stages belong to the Ticket's copied workflow, not to a global catalog.
_TERMINAL = """EXISTS (
    SELECT 1 FROM json_each({row}.workflow_json, '$.stages') AS stage
    WHERE json_extract(stage.value, '$.id') = {row}.stage
    AND json_extract(stage.value, '$.is_terminal') = 1
)"""


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute(
        "CREATE TABLE ticket_workspace_lifecycle (ticket_id TEXT PRIMARY KEY, "
        "ended_at INTEGER, deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)), "
        "generation INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE ticket_workspace_retirements (ticket_id TEXT NOT NULL, "
        "generation INTEGER NOT NULL, authorized_at INTEGER NOT NULL, "
        "PRIMARY KEY(ticket_id,generation))"
    )
    # No historical end time exists. Old ended Tickets receive the full grace period.
    conn.execute(
        "INSERT INTO ticket_workspace_lifecycle(ticket_id,ended_at,deleted) "
        "SELECT id, CASE WHEN "
        + _TERMINAL.format(row="tickets")
        + " THEN unixepoch() ELSE NULL END, 0 FROM tickets"
    )
    conn.execute(
        "CREATE TRIGGER ticket_workspace_insert AFTER INSERT ON tickets BEGIN "
        "INSERT INTO ticket_workspace_lifecycle(ticket_id,ended_at,deleted) VALUES "
        "(NEW.id, CASE WHEN "
        + _TERMINAL.format(row="NEW")
        + " THEN NEW.updated_at ELSE NULL END, 0) "
        "ON CONFLICT(ticket_id) DO UPDATE SET ended_at=excluded.ended_at,deleted=0; END"
    )
    conn.execute(
        "CREATE TRIGGER ticket_workspace_stage AFTER UPDATE OF stage,workflow_json ON tickets "
        "BEGIN UPDATE ticket_workspace_lifecycle SET ended_at=CASE WHEN "
        + _TERMINAL.format(row="NEW")
        + " THEN COALESCE(ended_at,NEW.updated_at) ELSE NULL END,deleted=0, "
        "generation=generation+CASE WHEN ended_at IS NOT NULL AND NOT "
        + _TERMINAL.format(row="NEW")
        + " AND EXISTS(SELECT 1 FROM ticket_workspace_retirements r "
        "WHERE r.ticket_id=NEW.id AND r.generation=ticket_workspace_lifecycle.generation) "
        "THEN 1 ELSE 0 END "
        "WHERE ticket_id=NEW.id; END"
    )
    conn.execute(
        "CREATE TRIGGER ticket_workspace_delete BEFORE DELETE ON tickets BEGIN "
        "INSERT INTO ticket_workspace_lifecycle(ticket_id,ended_at,deleted) "
        "VALUES(OLD.id,unixepoch(),1) ON CONFLICT(ticket_id) DO UPDATE SET "
        "ended_at=COALESCE(ended_at,unixepoch()),deleted=1; END"
    )

    # Provider intents and retirement grants share SQLite's writer boundary.
    # A stale native conversation cannot invoke a provider in a retired workspace.
    retired = """EXISTS(
        SELECT 1 FROM conversations c
        JOIN json_each(c.identity_environment_variables) owner
          ON json_extract(owner.value,'$[0]')='PLAN_TICKET_ID'
        LEFT JOIN json_each(c.identity_environment_variables) generation
          ON json_extract(generation.value,'$[0]')='PLAN_TICKET_WORKSPACE_GENERATION'
        JOIN ticket_workspace_retirements r
          ON r.ticket_id=json_extract(owner.value,'$[1]')
          AND r.generation=COALESCE(CAST(json_extract(generation.value,'$[1]') AS INTEGER),0)
        WHERE c.conversation_id=NEW.conversation_id
    )"""
    active = """((NEW.kind='turn' AND NEW.status IN ('intent','running','unknown'))
        OR (NEW.kind='attempt' AND NEW.status IN ('intent','unknown'))
        OR (NEW.kind='request' AND NEW.status IN ('pending','answering')))"""
    for operation in ("INSERT", "UPDATE"):
        conn.execute(
            f"CREATE TRIGGER ticket_workspace_activity_{operation.lower()} "
            f"BEFORE {operation} ON conversation_lifecycle_facts "
            f"WHEN {active} AND {retired} BEGIN "
            "SELECT RAISE(ABORT, 'Ticket workspace generation is retired'); END"
        )
        conn.execute(
            f"CREATE TRIGGER ticket_workspace_background_{operation.lower()} "
            f"BEFORE {operation} ON conversation_background_tasks "
            f"WHEN {retired} BEGIN "
            "SELECT RAISE(ABORT, 'Ticket workspace generation is retired'); END"
        )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
