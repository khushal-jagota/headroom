"""Ticket readiness — the predicate discovery and execution both check at
execution time. ``is_runnable(conn, ticket)`` answers "should the agent run the next step
of this Ticket right now?" from its Stage, fields, scope, and blockers alone.

Kept in its own module so the readiness loop and employee runner import it with no
cycle. It reaches only into ``tickets.logic`` and ``core.links``."""

from __future__ import annotations

import sqlite3

from planner.core import links as core_links
from planner.tickets.contracts import AtCap, Ticket
from planner.tickets.logic import machine
from planner.worker_types.contracts import WorkerTypeDefinition


def is_runnable(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    """True iff the ticket's next agent step should run: not terminal; has a gating field;
    no proposal already parked on that field awaiting a human; the scope permits a proposal
    (not at/beyond the ceiling with ``at_cap=stop`` — mirrors
    ``admission.check_agent_proposal``); and it is not blocked by an open ``blocks`` link.

    Every machine predicate is resolved against the ticket's OWN type definition (not the
    coding default), so a novel-stage type (e.g. new_worker at needs_stages) is classified
    against its real Stages instead of raising 'stage outside the linear order'."""
    if worker_type_definition.is_terminal(ticket.stage):
        return False
    if machine.has_pending_parked_proposal(ticket, worker_type_definition=worker_type_definition):
        return False  # parked awaiting a human decision
    gating = worker_type_definition.gating_field(ticket.stage)
    if gating is None:
        return False
    if (
        machine.at_or_beyond_ceiling(
            ticket.stage,
            ticket.ceiling,
            worker_type_definition=worker_type_definition,
        )
        and ticket.at_cap == AtCap.stop
    ):
        return False  # the scope says stop here
    if core_links.is_blocked(conn, ticket.id):
        return False
    return True
