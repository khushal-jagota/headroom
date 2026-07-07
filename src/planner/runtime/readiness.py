"""Ticket readiness — the pure predicate System A polls and System B re-checks at
execution time. ``is_runnable(conn, ticket)`` answers "should the agent run the next step
of this ticket right now?" from ticket state, fields, scope, and blockers alone.

Kept in its own module so both ``system_a`` and ``system_b`` import it with no cycle — it
reaches only into ``tickets.logic`` + ``core.links``, never into either system."""

from __future__ import annotations

import sqlite3

from planner.core import links as core_links
from planner.tickets.contracts import AtCap, Ticket
from planner.tickets.logic import fields_codec, machine


def is_runnable(conn: sqlite3.Connection, ticket: Ticket) -> bool:
    """True iff the ticket's next agent step should run: not terminal; has a gating field
    (``needs_review`` is human-approve-only, no agent step); no proposal already parked on
    the gating field awaiting a human; the scope permits a proposal (not at/beyond the
    ceiling with ``at_cap=stop`` — mirrors ``admission.check_agent_proposal``); and not
    blocked by an open ``blocks`` link."""
    if machine.is_terminal(ticket.state):
        return False
    gating = machine.gating_field(ticket.state)
    if gating is None:  # needs_review — the human approves it to done
        return False
    if fields_codec.get_slot(ticket.fields, gating).proposal is not None:
        return False  # parked awaiting a human decision
    if machine.at_or_beyond_ceiling(ticket.state, ticket.ceiling) and ticket.at_cap is AtCap.stop:
        return False  # the scope says stop here
    if core_links.is_blocked(conn, ticket.id):
        return False
    return True
