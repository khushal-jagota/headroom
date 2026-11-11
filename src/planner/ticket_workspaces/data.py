"""Durable end dates, including identities after Ticket deletion."""

import sqlite3

from planner.ticket_workspaces.contracts import RETENTION_SECONDS, WorkspaceState


def read_workspace_states(conn: sqlite3.Connection) -> dict[str, WorkspaceState]:
    return {
        str(row[0]): WorkspaceState(str(row[0]), row[1], bool(row[2]), int(row[3]))
        for row in conn.execute(
            "SELECT ticket_id, ended_at, deleted, generation FROM ticket_workspace_lifecycle"
        )
    }


def authorize_retirement(conn: sqlite3.Connection, state: WorkspaceState, now: int) -> bool:
    """Authorize only this ended generation, under the caller's write transaction."""
    current = read_workspace_states(conn).get(state.ticket_id)
    if current != state or state.ended_at is None or now < state.ended_at + RETENTION_SECONDS:
        return False
    if (
        conn.execute(
            "SELECT 1 FROM tickets WHERE id=? AND worker_step_claim='out'", (state.ticket_id,)
        ).fetchone()
        is not None
    ):
        return False
    if has_workspace_activity(conn, state.ticket_id):
        return False
    conn.execute(
        "INSERT OR IGNORE INTO ticket_workspace_retirements(ticket_id,generation,authorized_at) "
        "VALUES(?,?,?)",
        (state.ticket_id, state.generation, now),
    )
    return True


def ensure_workspace_generation(conn: sqlite3.Connection, ticket_id: str) -> WorkspaceState:
    """A later native admission must not reuse an already retired generation."""
    with conn:
        conn.execute(
            "UPDATE ticket_workspace_lifecycle SET generation=generation+1 "
            "WHERE ticket_id=? AND EXISTS(SELECT 1 FROM ticket_workspace_retirements r "
            "WHERE r.ticket_id=ticket_workspace_lifecycle.ticket_id "
            "AND r.generation=ticket_workspace_lifecycle.generation)",
            (ticket_id,),
        )
    return read_workspace_states(conn)[ticket_id]


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


def has_committed_retirement(conn: sqlite3.Connection, state: WorkspaceState) -> bool:
    """A detached old generation can be retried after the Ticket reopens."""
    return (
        conn.execute(
            "SELECT 1 FROM ticket_workspace_retirements WHERE ticket_id=? AND generation=?",
            (state.ticket_id, state.generation),
        ).fetchone()
        is not None
    )
