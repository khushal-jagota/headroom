"""Whether Panels may start this Ticket's next worker step right now.

This is the whole readiness decision and nothing else: it reads, it decides, and it
writes nothing. The loop runs it as a pre-filter and the claim writer runs it again
inside its write transaction, where the answer is final.
"""

from __future__ import annotations

import sqlite3

from planner.tickets import locale_configuration
from planner.tickets.contracts import StageOwnershipMode, Ticket, TicketStatus
from planner.tickets.logic import machine
from planner.worker_types.contracts import WorkerTypeDefinition


def worker_step_blocker(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> str | None:
    """Why Panels may not start this Ticket's next worker step, or nothing when it may.

    This is the readiness decision itself, and ``is_ready_for_worker_step`` is this
    answer read as a yes or a no. The sentence exists because a caller that asked for a
    start and got none has to be told what to do about it — a supervisor restart is the
    first such caller. The first refusal wins, so the order below is the order of the
    checks, not a ranking.
    """
    if ticket.is_backlogged:
        return "the Ticket is backlogged"
    if not locale_configuration.can_execute_locale(conn, ticket.locale):
        return (
            f"the Ticket locale is {ticket.locale.value}, so it cannot start on "
            f"this {locale_configuration.CURRENT_EXECUTION_LOCALE.value} machine"
        )
    if worker_type_definition.is_terminal(ticket.stage):
        return f"the Stage {ticket.stage} is terminal"
    ownership_mode = machine.stage_ownership_mode(
        ticket.stage,
        worker_type_definition=worker_type_definition,
    )
    # `empty` is the only startable control state.
    if ticket.ticket_status is not TicketStatus.empty:
        return f"the Ticket is at {ticket.ticket_status.value}, so no worker step is due"
    if ownership_mode is StageOwnershipMode.user:
        opened = conn.execute(
            "SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id = ? AND stage = ?",
            (ticket.id, ticket.stage),
        ).fetchone()
        if opened is not None:
            return "the user-owned Stage opener already ran for this Stage entry"
    if worker_type_definition.gating_field(ticket.stage) is None:
        return f"the Stage {ticket.stage} has no field for a worker to fill"
    from planner.conversation.backend_state import model_is_enabled
    from planner.conversation.contracts import require_conversation_backend_key

    defaults = worker_type_definition.worker_profile
    model = ticket.employee_launch_model or defaults.default_model
    if model is not None and not model_is_enabled(
        conn,
        require_conversation_backend_key(
            defaults.default_backend
            if ticket.employee_launch_model is None
            else ticket.employee_backend
        ),
        model,
    ):
        return f"the model {model} is disabled"
    return None


def is_ready_for_worker_step(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    """Whether Panels may automatically start this Ticket's next worker step now."""
    return (
        worker_step_blocker(
            conn,
            ticket,
            worker_type_definition=worker_type_definition,
        )
        is None
    )
