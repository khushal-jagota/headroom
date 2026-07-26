"""Durable, factual ACP projection used by Ticket Workspace cards."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from planner.core.db import connect
from planner.tickets.contracts import WorkspaceActivityState

_RESPONSE_COMPLETING_ACTIVITY_STATES = frozenset(
    {
        WorkspaceActivityState.thinking,
        WorkspaceActivityState.working,
        WorkspaceActivityState.compacting,
    }
)


@dataclass(frozen=True, slots=True)
class TicketConversationProjectionSnapshot:
    latest_activity_state: WorkspaceActivityState | None = None
    has_completed_response_awaiting_user: bool = False
    # Stays true once any Worker reply has completed (until reset), so a seen
    # reply is distinguishable from a Ticket that never had one.
    has_completed_response: bool = False
    has_pending_permission: bool = False


class TicketConversationProjection:
    """Own short-lived SQLite writes for one Ticket's ACP Workspace facts."""

    def __init__(
        self, db_path: str, *, now: Callable[[], int], busy_timeout_ms: int = 5000
    ) -> None:
        self._db_path = db_path
        self._now = now
        self._busy_timeout_ms = busy_timeout_ms

    def record_activity(self, ticket_id: str, state: str) -> bool:
        return self._write(
            ticket_id,
            latest_activity_state=WorkspaceActivityState(state),
        )

    def record_permission(self, ticket_id: str, pending: bool) -> bool:
        return self._write(ticket_id, has_pending_permission=pending)

    def enter_paired_on_human_prompt(self, ticket_id: str) -> None:
        """Courier a human typed message into the tickets domain's flip to paired.

        A no-op unless the Ticket is parked at awaiting_approval; the tickets-domain
        transition owns that guard.
        """
        import planner.tickets.data as tickets_data

        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            tickets_data.enter_paired_on_human_reply(conn, ticket_id, now=self._now())
        finally:
            conn.close()

    def reset(self, ticket_id: str) -> bool:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            conn.execute("BEGIN IMMEDIATE")
            previous = self._read_from_connection(conn, ticket_id)
            if previous is None:
                conn.execute("COMMIT")
                return False
            conn.execute(
                "DELETE FROM ticket_conversation_projections WHERE ticket_id = ?",
                (ticket_id,),
            )
            conn.execute("COMMIT")
            return True
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def read(self, ticket_id: str) -> TicketConversationProjectionSnapshot:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            return self._read_from_connection(
                conn, ticket_id
            ) or TicketConversationProjectionSnapshot()
        finally:
            conn.close()

    def _write(
        self,
        ticket_id: str,
        *,
        latest_activity_state: WorkspaceActivityState | None = None,
        has_pending_permission: bool | None = None,
        has_completed_response_awaiting_user: bool | None = None,
    ) -> bool:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            conn.execute("BEGIN IMMEDIATE")
            previous = self._read_from_connection(
                conn, ticket_id
            ) or TicketConversationProjectionSnapshot()
            next_state = (
                latest_activity_state
                if latest_activity_state is not None
                else previous.latest_activity_state
            )
            next_permission = (
                has_pending_permission
                if has_pending_permission is not None
                else previous.has_pending_permission
            )
            next_response = (
                has_completed_response_awaiting_user
                if has_completed_response_awaiting_user is not None
                else previous.has_completed_response_awaiting_user
            )
            if has_completed_response_awaiting_user is None:
                if latest_activity_state in _RESPONSE_COMPLETING_ACTIVITY_STATES:
                    next_response = False
                elif (
                    latest_activity_state is WorkspaceActivityState.idle
                    and previous.latest_activity_state in _RESPONSE_COMPLETING_ACTIVITY_STATES
                ):
                    next_response = True
            if latest_activity_state is not None:
                next_permission = previous.has_pending_permission
            current = TicketConversationProjectionSnapshot(
                latest_activity_state=next_state,
                has_completed_response_awaiting_user=next_response,
                has_completed_response=previous.has_completed_response or next_response,
                has_pending_permission=next_permission,
            )
            if current == previous:
                conn.execute("COMMIT")
                return False
            conn.execute(
                "INSERT INTO ticket_conversation_projections ("
                "ticket_id, latest_activity_state, has_completed_response_awaiting_user, "
                "has_completed_response, has_pending_permission, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(ticket_id) DO UPDATE SET "
                "latest_activity_state = excluded.latest_activity_state, "
                "has_completed_response_awaiting_user = "
                "excluded.has_completed_response_awaiting_user, "
                "has_completed_response = excluded.has_completed_response, "
                "has_pending_permission = excluded.has_pending_permission, "
                "updated_at = excluded.updated_at",
                (
                    ticket_id,
                    current.latest_activity_state.value
                    if current.latest_activity_state is not None
                    else None,
                    int(current.has_completed_response_awaiting_user),
                    int(current.has_completed_response),
                    int(current.has_pending_permission),
                    self._now(),
                ),
            )
            conn.execute("COMMIT")
            return True
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @staticmethod
    def _read_from_connection(
        conn: sqlite3.Connection, ticket_id: str
    ) -> TicketConversationProjectionSnapshot | None:
        row = conn.execute(
            "SELECT latest_activity_state, has_completed_response_awaiting_user, "
            "has_completed_response, has_pending_permission "
            "FROM ticket_conversation_projections WHERE ticket_id = ?",
            (ticket_id,),
        ).fetchone()
        if row is None:
            return None
        return TicketConversationProjectionSnapshot(
            latest_activity_state=(
                WorkspaceActivityState(str(row["latest_activity_state"]))
                if row["latest_activity_state"] is not None
                else None
            ),
            has_completed_response_awaiting_user=bool(
                row["has_completed_response_awaiting_user"]
            ),
            has_completed_response=bool(row["has_completed_response"]),
            has_pending_permission=bool(row["has_pending_permission"]),
        )
