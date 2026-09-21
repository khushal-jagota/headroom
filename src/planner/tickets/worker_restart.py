"""Start a Ticket's worker step again, on a worker that died.

One operation with one address. There was no ordinary route for it: reset and
re-configure exist separately, and nothing released the claim and started. Only an
Outcome manager could reach it, through its own wrapper. Khushal can now do it too.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable

from planner.conversation.contracts import ConversationSystem
from planner.core import authority
from planner.core.contracts import Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.runtime import conversation_start, worker_step_readiness
from planner.tickets import data as tickets_data
from planner.tickets.contracts import StageOwnershipMode, Ticket, WorkerStepClaim
from planner.tickets.logic import machine
from planner.worker_types.configuration import configured_worker_type_registry

# A worker step gets this long to prove it is alive before anyone may restart it.
WORKER_STEP_RESTART_FLOOR_SECONDS = 300


def require_restartable(
    conn: sqlite3.Connection,
    principal: Principal,
    ticket_id: str,
    *,
    now: int,
) -> Ticket:
    """Return the Ticket, once every reason not to restart it has been ruled out.

    Nothing is asked about whether the worker is alive, because nothing can answer it.
    ``is_running`` reads in-process bookkeeping that nothing clears when a backend dies,
    so a dead worker looks like a running one forever, and a guard on it would refuse the
    exact state a restart is for. Whoever stands above the Ticket decides that its worker
    is dead, from the conversation history they can already read, and the checks here
    bound what that decision can reach.
    """
    authority.require_above(conn, principal, authority.ticket(ticket_id))
    ticket = tickets_data.read_ticket(conn, ticket_id)
    worker_type_definition = configured_worker_type_registry().require(ticket.worker_type)
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
        age = now - ticket.worker_step_claim_changed_at
        if age < WORKER_STEP_RESTART_FLOOR_SECONDS:
            # The one bound on a restart loop: each restart resets this clock, so a
            # caller that keeps restarting has to wait out the floor every time.
            raise PlannerError(
                ErrorCode.validation,
                "this worker step is too young to restart",
                {
                    "ticket_id": ticket_id,
                    "age_seconds": age,
                    "floor_seconds": WORKER_STEP_RESTART_FLOOR_SECONDS,
                },
            )
        return ticket
    if ticket.worker_step_claim is WorkerStepClaim.errored:
        return ticket
    if ticket.conversation_id is None:
        # A restart whose start was refused lands here. Restarting again is how a
        # caller corrects the launch configuration it named the first time.
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
    write_employee_configuration: Callable[[sqlite3.Connection], None] | None,
    start_worker_step: Callable[[], Awaitable[bool]],
    planning_day_id: str,
    now: int,
) -> dict[str, object]:
    """Kill this Ticket's dead conversation, give the claim back, and start again.

    A reset on its own would leave the claim out, with no conversation and nothing that
    ever picks the Ticket up. Both halves happen here, and the caller sees one action.

    ``write_employee_configuration`` is already validated when it arrives: the caller
    resolved it against the backend registry and catalog before anything was killed, so a
    bad argument costs the Ticket nothing. It runs in the same transaction as the claim,
    after it, because the launch values unfreeze only once the claim is back.
    """
    ticket = require_restartable(conn, principal, ticket_id, now=now)
    killed_conversation_id = ticket.conversation_id
    killed_conversation_looked_running = (
        await conversations.is_running(killed_conversation_id)
        if killed_conversation_id is not None
        else False
    )
    if killed_conversation_id is not None:
        await conversation_start.reset_ticket_conversation(conversations, conn, ticket_id, now=now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if ticket.worker_step_claim is WorkerStepClaim.out:
            given_back = tickets_data.release_worker_step_claim(
                conn,
                ticket_id,
                expected_claim=ticket.worker_step_claim,
                expected_claim_revision=ticket.worker_step_claim_revision,
                now=now,
            )
            if not given_back:
                raise PlannerError(
                    ErrorCode.already_running,
                    "the Ticket moved while it was being restarted",
                    {"ticket_id": ticket_id},
                )
        elif ticket.worker_step_claim is WorkerStepClaim.errored:
            tickets_data.clear_ticket_error_for_restart(conn, ticket_id, now=now)
        if write_employee_configuration is not None:
            write_employee_configuration(conn)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    started = await start_worker_step()
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
        "started": started,
        "not_started_because": (
            None
            if started
            else worker_step_readiness.worker_step_blocker(
                conn,
                restarted,
                planning_day_id=planning_day_id,
                worker_type_definition=configured_worker_type_registry().require(
                    restarted.worker_type
                ),
            )
        ),
    }
