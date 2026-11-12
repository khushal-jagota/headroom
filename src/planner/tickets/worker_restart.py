"""Start a Ticket's worker step again, on a worker that died.

One operation with one address. There was no ordinary route for it: reset and
re-configure exist separately, and nothing released the claim and started. Only an
Manager could reach it, through its own wrapper. Khushal can now do it too.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from planner.conversation.child_custody import legacy_recovery_detail
from planner.conversation.contracts import ConversationSystem
from planner.conversation.start_admission import explicit_start
from planner.core.contracts import Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.core.task_settlement import run_on_worker_thread
from planner.runtime import conversation_start, worker_step_readiness
from planner.tickets import data as tickets_data
from planner.tickets import revision_feedback
from planner.tickets.contracts import (
    StageOwnershipMode,
    Ticket,
    TicketStatus,
    WorkerStepClaim,
)
from planner.tickets.logic import machine

if TYPE_CHECKING:
    from planner.runtime.worker_step_readiness_loop import WorkerStepStartResult


def require_restartable(
    conn: sqlite3.Connection,
    principal: Principal,
    ticket_id: str,
) -> Ticket:
    """Return the Ticket, once every reason not to restart it has been ruled out.

    Nothing is asked about whether the worker is alive, because nothing can answer it.
    ``is_running`` reads in-process bookkeeping that nothing clears when a backend dies,
    so a dead worker looks like a running one forever, and a guard on it would refuse the
    exact state a restart is for. Whoever stands above the Ticket decides that its worker
    is dead, from the conversation history they can already read, and the checks here
    bound what that decision can reach.
    """
    ticket = tickets_data.read_ticket(conn, ticket_id)
    worker_type_definition = ticket.workflow
    ownership_mode = machine.stage_ownership_mode(
        ticket.stage,
        worker_type_definition=worker_type_definition,
    )
    if ownership_mode is not StageOwnershipMode.worker:
        # A user-owned Stage opens one collaborative discussion, so no code can tell a
        # dead worker from a discussion waiting on the user. A restart there would kill a
        # conversation the user is in.
        raise PlannerError(
            ErrorCode.validation,
            "only a Worker-owned Stage has a worker step to restart",
            {"ticket_id": ticket_id, "stage": ticket.stage},
        )
    if ticket.worker_step_claim is WorkerStepClaim.out:
        return ticket
    if ticket.worker_step_claim is WorkerStepClaim.errored:
        return ticket
    if ticket.conversation_id is None:
        # A restart whose start was refused lands here. Restarting again is how a
        # caller corrects the launch configuration it named the first time.
        return ticket
    if (
        ticket.ticket_status is TicketStatus.empty
        and revision_feedback.snapshot(conn, ticket_id) is not None
    ):
        # Revision work keeps its conversation, so restart only has to rearm it.
        return ticket
    raise PlannerError(
        ErrorCode.validation,
        "the Ticket has no worker step out to restart",
        {"ticket_id": ticket_id, "ticket_status": ticket.ticket_status.value},
    )


async def restart_worker(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    principal: Principal,
    ticket_id: str,
    *,
    start_worker_step: Callable[[], Awaitable[WorkerStepStartResult]],
    now: Callable[[], int],
    write_employee_configuration: Callable[[sqlite3.Connection, int], None] | None = None,
) -> dict[str, object]:
    """Kill this Ticket's dead conversation, give the claim back, and start again.

    A reset on its own would leave the claim out, with no conversation and nothing that
    ever picks the Ticket up. Both halves happen here, and the caller sees one action.

    The configuration is already validated when it arrives: the caller checked
    the backend name and disabled-model policy before anything was killed, so a
    bad argument costs the Ticket nothing. It runs in the same transaction as the claim,
    after it, because the launch values unfreeze only once the claim is back.

    Every database step runs on a worker thread, one at a time on the caller's
    connection. The link lock, the kill and the start are the conversation system's and
    stay on the event loop.
    """
    (
        killed_conversation_id,
        killed_conversation_looked_running,
    ) = await _prepare_worker_restart(
        conversations,
        conn,
        principal,
        ticket_id,
        write_employee_configuration=write_employee_configuration,
        now=now,
    )
    # Start obtains fresh read ownership, after mutation ownership ends.
    with explicit_start():
        start_result = await start_worker_step()
    return await run_on_worker_thread(
        _restart_result,
        conn,
        ticket_id,
        start_result,
        killed_conversation_id,
        killed_conversation_looked_running,
    )


async def _prepare_worker_restart(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    principal: Principal,
    ticket_id: str,
    *,
    write_employee_configuration: Callable[[sqlite3.Connection, int], None] | None,
    now: Callable[[], int],
) -> tuple[str | None, bool]:
    ticket = await run_on_worker_thread(require_restartable, conn, principal, ticket_id)
    rearming_idle_revision = (
        ticket.worker_step_claim is WorkerStepClaim.none and ticket.conversation_id is not None
    )
    if rearming_idle_revision and write_employee_configuration is not None:
        raise PlannerError(
            ErrorCode.already_running,
            "Employee configuration is frozen while a conversation or a worker step holds it",
            {"ticket_id": ticket_id},
        )
    killed_conversation_id = ticket.conversation_id
    killed_conversation_looked_running = False
    async with conversation_start.conversation_link_lock(f"ticket:{ticket_id}"):
        ticket = await run_on_worker_thread(require_restartable, conn, principal, ticket_id)
        killed_conversation_id = ticket.conversation_id
        if killed_conversation_id is not None:
            killed_conversation_looked_running = await conversations.is_running(
                killed_conversation_id
            )
            # Live-state bookkeeping can be stale, so it cannot decide admission. Kill
            # the exact old traffic without a database write lock. A later send can resume
            # this conversation if the Ticket moves while kill is awaited.
            await conversations.kill(killed_conversation_id)

        source_retirement_proven = False
        if killed_conversation_id is not None:
            source_retirement_proven = await conversations.retirement_is_confirmed(
                killed_conversation_id
            )
            if not source_retirement_proven:
                repair = getattr(conversations, "repair_legacy_source", None)
                if repair is not None:
                    await repair(killed_conversation_id)
                    source_retirement_proven = await conversations.retirement_is_confirmed(
                        killed_conversation_id
                    )
            if not source_retirement_proven:
                legacy_detail = await run_on_worker_thread(
                    legacy_recovery_detail, conn, killed_conversation_id
                )
                raise PlannerError(
                    ErrorCode.validation,
                    legacy_detail
                    or (
                        "source retirement is unresolved; "
                        "executor or operator settlement is required"
                    ),
                    {
                        "retirement_started": True,
                        "source_retired": None,
                        "reason": "context_changed",
                        "corrective_action": "verify_launch_context",
                    },
                )
        try:
            await run_on_worker_thread(
                _give_the_claim_back_and_write_configuration,
                conn,
                principal,
                ticket_id,
                write_employee_configuration=write_employee_configuration,
                now=now(),
            )
        except PlannerError as error:
            raise PlannerError(
                error.code,
                error.message,
                {
                    **error.detail,
                    "retirement_started": killed_conversation_id is not None,
                    "source_retired": True if source_retirement_proven else None,
                },
            ) from error
    return killed_conversation_id, killed_conversation_looked_running


def _give_the_claim_back_and_write_configuration(
    conn: sqlite3.Connection,
    principal: Principal,
    ticket_id: str,
    *,
    write_employee_configuration: Callable[[sqlite3.Connection, int], None] | None,
    now: int,
) -> None:
    """Give the claim back and write the configuration, as one transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        ticket = require_restartable(conn, principal, ticket_id)
        if ticket.worker_step_claim in {
            WorkerStepClaim.out,
            WorkerStepClaim.errored,
        }:
            tickets_data.release_worker_step_claim(
                conn,
                ticket_id,
                expected_claim=ticket.worker_step_claim,
                expected_claim_revision=ticket.worker_step_claim_revision,
                now=now,
            )
            if ticket.conversation_id is not None:
                tickets_data.clear_ticket_conversation_link(
                    conn,
                    ticket_id,
                    expected_conversation_id=ticket.conversation_id,
                    now=now,
                )
        if write_employee_configuration is not None:
            write_employee_configuration(conn, now)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _restart_result(
    conn: sqlite3.Connection,
    ticket_id: str,
    start_result: WorkerStepStartResult,
    killed_conversation_id: str | None,
    killed_conversation_looked_running: bool,
) -> dict[str, object]:
    restarted = tickets_data.read_ticket(conn, ticket_id)
    return {
        "ticket_id": ticket_id,
        "killed_conversation_id": killed_conversation_id,
        "killed_conversation_looked_running": killed_conversation_looked_running,
        "employee_configuration": {
            "employee_backend": restarted.employee_backend,
            "employee_launch_model": restarted.employee_launch_model,
            "employee_launch_reasoning_effort": restarted.employee_launch_reasoning_effort,
        },
        "ticket_status": restarted.ticket_status.value,
        "conversation_id": restarted.conversation_id,
        "started": start_result.started,
        "delivery_fate": start_result.delivery_fate,
        "not_started_because": (
            None
            if start_result.started or start_result.delivery_fate is not None
            else worker_step_readiness.worker_step_blocker(
                conn,
                restarted,
                worker_type_definition=restarted.workflow,
            )
        ),
    }
