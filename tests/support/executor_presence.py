"""Make a Mac executor process current the way a lease does, without a Mac.

The fence, the mailbox and child assignment all read the presence row. Tests that need
a current process write one accepted lease, either through the real lease handler or,
for a test that has no event loop, straight into the row.
"""

from __future__ import annotations

from uuid import uuid4

from planner.core.db import connect
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.clock import now_ms
from planner.executor.protocol import LeaseRequest, WaitingConversation, WorkingTurn
from planner.executor.vps_presence_store import accept_lease_in_transaction


def mac_identity(incarnation: str | None = None) -> ExecutorIdentity:
    return ExecutorIdentity(
        PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, incarnation or str(uuid4())
    )


def lease_request(
    identity: ExecutorIdentity,
    *,
    waiting: tuple[WaitingConversation, ...] = (),
    truncated: bool = False,
    working: tuple[WorkingTurn, ...] = (),
    queue_depth: int | None = None,
    going_to_sleep: bool = False,
) -> LeaseRequest:
    return LeaseRequest(
        identity=identity,
        process_id=4242,
        mac_wall_clock_ms=now_ms(),
        queue_depth=len(waiting) if queue_depth is None else queue_depth,
        oldest_item_age_ms=None,
        waiting_conversations=waiting,
        waiting_conversations_truncated=truncated,
        working_turns=working,
        admission_stopped=False,
        going_to_sleep=going_to_sleep,
    )


def make_current(db_path: str, identity: ExecutorIdentity, at_ms: int | None = None) -> None:
    """Commit one accepted lease of identity, as the lease handler does (blocking).

    A VpsExecutor built before this call keeps its own presence copy; lease through
    the endpoint (endpoint.lease) when one is already running.
    """
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        accept_lease_in_transaction(
            connection, lease_request(identity), now_ms() if at_ms is None else at_ms
        )
        connection.commit()
    finally:
        connection.close()
