"""Durable Ticket clocks and grants for one machine's physical workspace."""

import sqlite3

from planner.ticket_workspaces.contracts import (
    RETENTION_SECONDS,
    WorkspaceIdentity,
    WorkspaceState,
)


def read_workspace_states(conn: sqlite3.Connection) -> dict[str, WorkspaceState]:
    return {
        str(row[0]): WorkspaceState(str(row[0]), row[1], bool(row[2]))
        for row in conn.execute(
            "SELECT ticket_id, ended_at, deleted FROM ticket_workspace_lifecycle"
        )
    }


def ensure_workspace_activity(
    conn: sqlite3.Connection,
    ticket_id: str,
    now: int,
) -> WorkspaceState:
    """Actual new work in a Done Ticket gets a new seven-day retention period."""
    with conn:
        conn.execute(
            "UPDATE ticket_workspace_lifecycle SET ended_at=? "
            "WHERE ticket_id=? AND ended_at IS NOT NULL AND deleted=0",
            (now, ticket_id),
        )
    return read_workspace_states(conn)[ticket_id]


def register_workspace(
    conn: sqlite3.Connection,
    ticket_id: str,
    conversation_id: str,
    identity: WorkspaceIdentity,
) -> None:
    """Bind a native conversation to the exact local folder it will use."""
    conn.execute(
        "INSERT INTO ticket_workspace_conversations "
        "(conversation_id,ticket_id,machine_id,workspace_id) "
        "VALUES(?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET "
        "machine_id=excluded.machine_id,workspace_id=excluded.workspace_id",
        (conversation_id, ticket_id, identity.machine_id, identity.workspace_id),
    )
    # Registration proves the same folder still exists at its attached source path.
    # A failed cleanup must not displace that folder on the next native start.
    conn.execute(
        "DELETE FROM ticket_workspace_retirements "
        "WHERE ticket_id=? AND machine_id=? AND workspace_id=?",
        (ticket_id, identity.machine_id, identity.workspace_id),
    )


def conversation_workspace_retired(conn: sqlite3.Connection, conversation_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM ticket_workspace_conversations w "
            "JOIN ticket_workspace_retirements r USING(ticket_id,machine_id,workspace_id) "
            "WHERE w.conversation_id=?",
            (conversation_id,),
        ).fetchone()
        is not None
    )


def authorize_retirement(
    conn: sqlite3.Connection,
    state: WorkspaceState,
    now: int,
    identity: WorkspaceIdentity,
) -> bool:
    """Authorize only this ended Ticket and this exact physical folder."""
    current = read_workspace_states(conn).get(state.ticket_id)
    if current != state or state.ended_at is None or now < state.ended_at + RETENTION_SECONDS:
        return False
    if conn.execute(
        "SELECT 1 FROM tickets WHERE id=? AND worker_step_claim='out'", (state.ticket_id,)
    ).fetchone() is not None or has_workspace_activity(conn, state.ticket_id):
        return False
    conn.execute(
        "INSERT OR IGNORE INTO ticket_workspace_retirements "
        "(ticket_id,machine_id,workspace_id,authorized_at) VALUES(?,?,?,?)",
        (state.ticket_id, identity.machine_id, identity.workspace_id, now),
    )
    return True


def has_committed_retirement(
    conn: sqlite3.Connection,
    state: WorkspaceState,
    identity: WorkspaceIdentity,
) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM ticket_workspace_retirements "
            "WHERE ticket_id=? AND machine_id=? AND workspace_id=?",
            (state.ticket_id, identity.machine_id, identity.workspace_id),
        ).fetchone()
        is not None
    )


def has_workspace_activity(conn: sqlite3.Connection, ticket_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM conversations c JOIN json_each(c.identity_environment_variables) owner "
            "ON json_extract(owner.value,'$[0]')='PLAN_TICKET_ID' "
            "WHERE json_extract(owner.value,'$[1]')=? AND (EXISTS("
            "SELECT 1 FROM conversation_lifecycle_facts f "
            "WHERE f.conversation_id=c.conversation_id "
            "AND ((f.kind='turn' AND f.status IN ('intent','running','unknown')) "
            "OR (f.kind='attempt' AND f.status IN ('intent','unknown')) "
            "OR (f.kind='request' AND f.status IN ('pending','answering')))) OR EXISTS("
            "SELECT 1 FROM conversation_background_tasks b "
            "WHERE b.conversation_id=c.conversation_id)) "
            "LIMIT 1",
            (ticket_id,),
        ).fetchone()
        is not None
    )
