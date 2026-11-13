"""Restart a Ticket's Worker: kill its conversation, unlink it, start a fresh step.

One operation with one address, shared by the CLI and the API. A successful kill
establishes that the old Worker ended. If cleanup cannot establish that, kill raises
before the claim, conversation link, or launch configuration changes. A Mac Ticket
refuses a restart while the Mac is away because the kill cannot reach it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from planner.conversation.contracts import ConversationSystem
from planner.conversation.start_admission import explicit_start
from planner.core.contracts import Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.core.task_settlement import run_on_worker_thread
from planner.executor.clock import now_ms
from planner.machines.contracts import HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID
from planner.machines.data import read_machine
from planner.runtime import conversation_start, worker_step_readiness
from planner.tickets import data as tickets_data
from planner.tickets import decision_messages, locale_configuration
from planner.tickets.contracts import (
    StageOwnershipMode,
    Ticket,
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
    """Return the Ticket, unless its Stage is the user's or its Mac is away."""
    ticket = tickets_data.read_ticket(conn, ticket_id)
    ownership_mode = machine.stage_ownership_mode(
        ticket.stage,
        worker_type_definition=ticket.workflow,
    )
    if ownership_mode is not StageOwnershipMode.worker:
        # A user-owned Stage runs its opener once per Stage entry. A restart there would
        # kill the conversation the user is in and start nothing.
        raise PlannerError(
            ErrorCode.validation,
            "only a Worker-owned Stage has a worker step to restart",
            {"ticket_id": ticket_id, "stage": ticket.stage},
        )
    if ticket.machine_id != HOME_MACHINE_ID and not locale_configuration.mac_is_present(
        conn, now_ms(), ticket.machine_id
    ):
        # A stop cannot reach a Mac that is away, so its old Worker could outlive the
        # restart. The readiness loop would not start the fresh step either.
        destination = read_machine(conn, ticket.machine_id)
        waiting = (locale_configuration.WAITING_FOR_THE_MAC
                   if ticket.machine_id == ORIGINAL_MAC_MACHINE_ID and destination.name == "Mac"
                   else f"Waiting for {destination.name}")
        raise PlannerError(
            ErrorCode.validation,
            f"{waiting}: restart this Ticket once it is back",
            {"ticket_id": ticket_id},
        )
    return ticket


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
    """Kill this Ticket's conversation, unlink it, give back any claim, and start again.

    Whatever the claim is, a successful kill permits the restart. A failed kill
    preserves the claim, link, and configuration, and prevents a fresh start. If the
    Ticket awaits approval, the kill and unlink happen and the result explains why
    nothing started.

    The configuration is already validated when it arrives: the caller checked
    the backend name and the model check before anything was killed, so a
    bad argument costs the Ticket nothing. It runs in the same transaction as the claim,
    after it, because the launch values unfreeze only once the claim is back.

    Every database step runs on a worker thread, one at a time on the caller's
    connection. The link lock, the kill and the start are the conversation system's and
    stay on the event loop.
    """
    async with conversation_start.conversation_link_lock(f"ticket:{ticket_id}"):
        ticket = await run_on_worker_thread(require_restartable, conn, principal, ticket_id)
        killed_conversation_id = ticket.conversation_id
        killed_conversation_looked_running = False
        revision_restarted_in_fresh_conversation = False
        if killed_conversation_id is not None:
            revision_restarted_in_fresh_conversation = bool(
                await run_on_worker_thread(decision_messages.has_undelivered, conn, ticket_id)
            )
            killed_conversation_looked_running = await conversations.is_running(
                killed_conversation_id
            )
            # kill owns cleanup proof. Its refusal must precede this transaction,
            # so the Ticket still points at the Worker whose cleanup needs a recheck.
            await conversations.kill(killed_conversation_id)
        await run_on_worker_thread(
            _unlink_give_back_the_claim_and_write_configuration,
            conn,
            ticket_id,
            killed_conversation_id=killed_conversation_id,
            write_employee_configuration=write_employee_configuration,
            now=now(),
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
        revision_restarted_in_fresh_conversation,
    )


def _unlink_give_back_the_claim_and_write_configuration(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    killed_conversation_id: str | None,
    write_employee_configuration: Callable[[sqlite3.Connection, int], None] | None,
    now: int,
) -> None:
    """Unlink the killed conversation, give back any claim, and write the configuration.

    One transaction. The Ticket is read again inside it, because a Worker can file its
    proposal while the kill is awaited, and that gives the claim back by itself.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        ticket = tickets_data.read_ticket(conn, ticket_id)
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
        if killed_conversation_id is not None:
            tickets_data.clear_ticket_conversation_link(
                conn,
                ticket_id,
                expected_conversation_id=killed_conversation_id,
                now=now,
            )
        decision_messages.rearm_after_restart(conn, ticket_id)
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
    revision_restarted_in_fresh_conversation: bool,
) -> dict[str, object]:
    restarted = tickets_data.read_ticket(conn, ticket_id)
    return {
        "ticket_id": ticket_id,
        "killed_conversation_id": killed_conversation_id,
        "killed_conversation_looked_running": killed_conversation_looked_running,
        # The old working context is gone. Only the revision feedback carries over.
        "revision_restarted_in_fresh_conversation": revision_restarted_in_fresh_conversation,
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
