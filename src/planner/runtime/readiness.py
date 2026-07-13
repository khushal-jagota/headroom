"""Ticket readiness — the predicate discovery and execution both check at
execution time. ``is_runnable(conn, ticket)`` answers "should the agent run the next step
of this ticket right now?" from ticket state, fields, scope, and blockers alone.

Kept in its own module so the readiness loop and employee runner import it with no
cycle. It reaches only into ``tickets.logic`` and ``core.links``."""

from __future__ import annotations

import sqlite3

from planner.core import links as core_links
from planner.tickets.contracts import AtCap, Ticket
from planner.tickets.logic import coding_bridge, machine


def is_runnable(conn: sqlite3.Connection, ticket: Ticket) -> bool:
    """True iff the ticket's next agent step should run: not terminal; has a gating field;
    no proposal already parked on that field awaiting a human; the scope permits a proposal
    (not at/beyond the ceiling with ``at_cap=stop`` — mirrors
    ``admission.check_agent_proposal``); and it is not blocked by an open ``blocks`` link.

    Every machine predicate is resolved against the ticket's OWN type definition (not the
    coding default), so a novel-stage type (e.g. new_worker at needs_stages) is classified
    against its real stages instead of raising 'state outside the linear order'."""
    defn = coding_bridge.require(ticket.ticket_type)
    if machine.is_terminal(ticket.state, definition=defn):
        return False
    if machine.has_pending_parked_proposal(ticket, definition=defn):
        return False  # parked awaiting a human decision
    gating = machine.gating_field(ticket.state, definition=defn)
    if gating is None:
        return False
    if (
        machine.at_or_beyond_ceiling(ticket.state, ticket.ceiling, definition=defn)
        and ticket.at_cap == AtCap.stop
    ):
        return False  # the scope says stop here
    if core_links.is_blocked(conn, ticket.id):
        return False
    return True
